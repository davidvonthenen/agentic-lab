"""Offline tests for Episode 1 helpers, settings, and Make targets.

These tests do not build containers, install requirements, download real weights,
start Flask, or run inference. Hugging Face calls use test doubles and tiny fake
files; those files are not model artifacts suitable for inference.

Run from the repository root:
    python -m unittest discover -s 1-introduction/tests -v
"""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

EPISODE = Path(__file__).resolve().parents[1]
SERVICES = ("slm_service", "orch_service")


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import test subject {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


downloader = load_module("download_models", EPISODE / "download_models.py")
verifier = load_module("episode1_verifier", EPISODE / "verify_environment.py")
CONFIGS = [
    load_module(f"episode1_{service}_config", EPISODE / service / "common/config.py")
    for service in SERVICES
]


class RuntimeSettingsTests(unittest.TestCase):
    def settings(self, module, system: str, machine: str, env: dict[str, str]):
        with patch.dict(os.environ, {"HOME": str(Path.home()), **env}, clear=True), \
             patch.object(module.platform, "system", return_value=system), \
             patch.object(module.platform, "machine", return_value=machine):
            return module.load_settings()

    def test_default_runtime_on_supported_platforms(self):
        cases = (("Linux", "x86_64", "gguf"), ("Linux", "aarch64", "gguf"),
                 ("Darwin", "arm64", "mlx"), ("Darwin", "x86_64", "gguf"))
        for module in CONFIGS:
            for system, machine, expected in cases:
                with self.subTest(module=module.__name__, system=system, machine=machine):
                    self.assertEqual(self.settings(module, system, machine, {}).llm_runtime, expected)

    def test_explicit_runtime_is_not_discarded(self):
        for module in CONFIGS:
            for runtime in ("gguf", "mlx"):
                with self.subTest(module=module.__name__, runtime=runtime):
                    settings = self.settings(module, "Darwin", "arm64", {"LLM_RUNTIME": runtime})
                    self.assertEqual(settings.llm_runtime, runtime)

    def test_explicit_runtime_is_normalized(self):
        for module in CONFIGS:
            self.assertEqual(self.settings(module, "Darwin", "arm64", {"LLM_RUNTIME": " MLX "}).llm_runtime, "mlx")

    def test_blank_runtime_uses_platform_default(self):
        for module in CONFIGS:
            self.assertEqual(self.settings(module, "Linux", "aarch64", {"LLM_RUNTIME": " "}).llm_runtime, "gguf")

    def test_invalid_runtime_is_rejected(self):
        for module in CONFIGS:
            with self.assertRaisesRegex(ValueError, "LLM_RUNTIME"):
                self.settings(module, "Linux", "x86_64", {"LLM_RUNTIME": "invalid"})

    def test_container_memory_and_cpu_overrides(self):
        env = {"LLM_RUNTIME": "gguf", "LLAMA_N_GPU_LAYERS": "0", "LLAMA_N_THREADS": "4", "LLAMA_CTX": "8192"}
        for module in CONFIGS:
            settings = self.settings(module, "Linux", "aarch64", env)
            self.assertEqual((settings.llama_n_gpu_layers, settings.llama_n_threads, settings.llama_ctx), (0, 4, 8192))

    def test_original_model_defaults_match_downloads(self):
        expected = (
            ("Qwen2.5-7B-Instruct-1M-Q5_K_M.gguf", "Qwen2.5-7B-Instruct-1M-4bit"),
            ("Nemotron-Orchestrator-8B-q4_k_m.gguf", "Orchestrator-8B-4bit"),
        )
        for module, (gguf, mlx) in zip(CONFIGS, expected):
            settings = self.settings(module, "Linux", "x86_64", {})
            self.assertEqual(Path(settings.llama_model_path).name, gguf)
            self.assertEqual(Path(settings.mlx_model_path).name, mlx)
            self.assertEqual(Path(settings.llama_model_path).parent, Path.home() / "models")


class SourceContractTests(unittest.TestCase):
    def test_all_episode_python_parses_with_python312_grammar(self):
        for path in EPISODE.rglob("*.py"):
            with self.subTest(path=path.relative_to(EPISODE)):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 12))

    def test_no_unconditional_import_of_unused_backend(self):
        for service in SERVICES:
            tree = ast.parse((EPISODE / service / f"{service}.py").read_text())
            top_imports = []
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    top_imports.append(node.module or "")
                elif isinstance(node, ast.Import):
                    top_imports.extend(item.name for item in node.names)
            self.assertFalse(any(name.startswith(("mlx", "llama_cpp")) for name in top_imports))

    def test_selected_backend_imports_remain_in_loader_functions(self):
        for service in SERVICES:
            tree = ast.parse((EPISODE / service / f"{service}.py").read_text())
            functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
            for function, module in (("_load_local_gguf_llm", "llama_cpp"), ("_load_local_mlx_llm", "mlx_lm")):
                imports = [node.module for node in ast.walk(functions[function]) if isinstance(node, ast.ImportFrom)]
                self.assertIn(module, imports)

    def test_service_listener_and_health_default_ports_agree(self):
        for service, port in (("slm_service", "8001"), ("orch_service", "8002")):
            tree = ast.parse((EPISODE / service / f"{service}.py").read_text())
            defaults = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or len(node.args) != 2:
                    continue
                if isinstance(node.func, ast.Attribute) and node.func.attr == "getenv":
                    first, second = node.args
                    if isinstance(first, ast.Constant) and first.value == "LLM_SERVER_PORT":
                        defaults.append(second.value)
            self.assertEqual(defaults, [port, port])


@unittest.skipUnless(shutil.which("make"), "GNU Make is not available")
class MakefileTests(unittest.TestCase):
    def test_generic_and_named_targets_invoke_correct_script(self):
        for service, alias in (("slm_service", "slm-service"), ("orch_service", "orch-service")):
            for target in ("service", alias):
                with self.subTest(service=service, target=target):
                    result = subprocess.run(
                        ["make", "--no-print-directory", "-n", target],
                        cwd=EPISODE / service, check=True, capture_output=True, text=True,
                    )
                    self.assertEqual(result.stdout.strip(), f"python {service}.py")

    def test_targets_export_independent_service_configuration(self):
        extra_rule = "inspect-env:\n\t@printf '%s\\n' \"$$LLM_SERVER_HOST\" \"$$LLM_SERVER_PORT\" \"$$LLM_SERVER_MODEL\" \"$$LLAMA_CTX\"\n"
        for service, port, label in (
            ("slm_service", "8001", "Qwen/Qwen2.5-7B-Instruct-1M"),
            ("orch_service", "8002", "nvidia/Nemotron-Orchestrator-8B"),
        ):
            clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "LLAMA_"))}
            result = subprocess.run(
                ["make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-", "inspect-env"],
                input=extra_rule, cwd=EPISODE / service, env=clean_env,
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(result.stdout.splitlines(), ["0.0.0.0", port, label, "8192"])


class ModelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = {"info": [], "gguf": [], "mlx": []}
        calls = self.calls

        class FakeApi:
            def model_info(self, repo_id, revision):
                calls["info"].append((repo_id, revision))
                return SimpleNamespace(sha=hashlib.sha1(repo_id.encode()).hexdigest())

        def fake_file(**kwargs):
            calls["gguf"].append(kwargs)
            target = Path(kwargs["local_dir"]) / kwargs["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"GGUFtest fixture, not an actual model")
            return str(target)

        def fake_snapshot(**kwargs):
            calls["mlx"].append(kwargs)
            target = Path(kwargs["local_dir"])
            target.mkdir(parents=True, exist_ok=True)
            (target / "config.json").write_text("{}")
            (target / "model.safetensors").write_bytes(b"test fixture, not an actual model")
            (target / ".cache").mkdir(exist_ok=True)
            (target / ".cache" / "ignored").write_text("cache metadata")
            return str(target)

        self.fake = ModuleType("huggingface_hub")
        self.fake.HfApi = FakeApi
        self.fake.hf_hub_download = fake_file
        self.fake.snapshot_download = fake_snapshot

    def download(self, selection="all", env=None):
        with patch.dict(sys.modules, {"huggingface_hub": self.fake}), \
             patch.dict(os.environ, env or {}, clear=True), redirect_stdout(io.StringIO()):
            return downloader.download_models(self.root, selection)

    def test_catalog_contains_exact_requested_artifacts(self):
        expected = {
            ("mlx-community/Qwen2.5-7B-Instruct-1M-4bit", "Qwen2.5-7B-Instruct-1M-4bit"),
            ("bartowski/Qwen2.5-7B-Instruct-1M-GGUF", "Qwen2.5-7B-Instruct-1M-Q5_K_M.gguf"),
            ("Mungert/Nemotron-Orchestrator-8B-GGUF", "Nemotron-Orchestrator-8B-q4_k_m.gguf"),
            ("mlx-community/Orchestrator-8B-4bit", "Orchestrator-8B-4bit"),
        }
        self.assertEqual({(item.repo_id, item.local_name) for item in downloader.MODELS}, expected)

    def test_all_uses_two_file_downloads_and_two_full_snapshots(self):
        self.download()
        self.assertEqual(len(self.calls["gguf"]), 2)
        self.assertEqual(len(self.calls["mlx"]), 2)
        expected_files = {item.local_name for item in downloader.MODELS if item.kind == "gguf"}
        self.assertEqual({call["filename"] for call in self.calls["gguf"]}, expected_files)
        self.assertTrue(all("allow_patterns" not in call for call in self.calls["mlx"]))

    def test_downloads_use_resolved_commits_and_manifest_records_files(self):
        manifest_path = self.download(env={"QWEN_GGUF_REVISION": "reviewed-tag"})
        payload = json.loads(manifest_path.read_text())
        self.assertEqual(len(payload["models"]), 4)
        for record in payload["models"]:
            self.assertEqual(record["resolved_revision"], hashlib.sha1(record["repo_id"].encode()).hexdigest())
            self.assertTrue(record["files"])
            self.assertTrue(all(item["bytes"] > 0 for item in record["files"]))
            self.assertFalse(any(".cache" in item["path"] for item in record["files"]))
        qwen = next(item for item in payload["models"] if item["repo_id"].startswith("bartowski/"))
        self.assertEqual(qwen["requested_revision"], "reviewed-tag")
        for call in self.calls["gguf"] + self.calls["mlx"]:
            self.assertEqual(call["revision"], hashlib.sha1(call["repo_id"].encode()).hexdigest())
        self.assertFalse((self.root / "model-manifest.json.tmp").exists())

    def test_selection_keeps_other_manifest_entries(self):
        self.download("gguf")
        self.assertEqual(len(self.calls["mlx"]), 0)
        manifest_path = self.download("mlx")
        self.assertEqual(len(json.loads(manifest_path.read_text())["models"]), 4)
        self.assertEqual(len(self.calls["gguf"]), 2)
        self.assertEqual(len(self.calls["mlx"]), 2)

    def test_invalid_selection_fails_before_api_calls(self):
        with self.assertRaises(ValueError):
            self.download("invalid")
        self.assertEqual(self.calls, {"info": [], "gguf": [], "mlx": []})

    def test_missing_gguf_is_rejected(self):
        spec = next(item for item in downloader.MODELS if item.kind == "gguf")
        with self.assertRaisesRegex(ValueError, "Missing or empty"):
            downloader.validate_artifact(spec, self.root)

    def test_non_gguf_content_is_rejected(self):
        spec = next(item for item in downloader.MODELS if item.kind == "gguf")
        (self.root / spec.local_name).write_bytes(b"<html>not weights</html>")
        with self.assertRaisesRegex(ValueError, "Not a GGUF"):
            downloader.validate_artifact(spec, self.root)

    def test_empty_mlx_weights_are_rejected(self):
        spec = next(item for item in downloader.MODELS if item.kind == "mlx")
        target = self.root / spec.local_name
        target.mkdir()
        (target / "config.json").write_text("{}")
        (target / "model.safetensors").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "Missing or empty MLX"):
            downloader.validate_artifact(spec, self.root)


class DependencyCheckTests(unittest.TestCase):
    def requirements(self, text):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "requirements.txt"
        path.write_text(text)
        return path

    def test_satisfied_requirement_with_mocked_metadata(self):
        path = self.requirements("# heading\nexample>=1,<2\n")
        with patch.object(verifier.metadata, "version", return_value="1.5"):
            failures, count = verifier.check_dependencies(path)
        self.assertEqual((failures, count), ([], 1))

    def test_out_of_range_version_is_reported(self):
        path = self.requirements("example>=1,<2\n")
        with patch.object(verifier.metadata, "version", return_value="2.0"):
            failures, count = verifier.check_dependencies(path)
        self.assertEqual(count, 1)
        self.assertIn("installed 2.0", failures[0])

    def test_missing_package_is_reported(self):
        path = self.requirements("missing-package==1.0\n")
        with patch.object(verifier.metadata, "version", side_effect=verifier.metadata.PackageNotFoundError):
            failures, count = verifier.check_dependencies(path)
        self.assertEqual((failures, count), (["Missing dependency: missing-package"], 1))

    def test_inapplicable_marker_is_skipped(self):
        path = self.requirements('example==1.0; platform_system == "NonexistentTestOS"\n')
        with patch.object(verifier.metadata, "version") as lookup:
            failures, count = verifier.check_dependencies(path)
        self.assertEqual((failures, count), ([], 0))
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
