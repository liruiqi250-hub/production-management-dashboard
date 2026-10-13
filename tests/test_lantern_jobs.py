"""彩灯后台整理回归：仅临时合成台账，来源及 Codex 进程全部隔离。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.jobs import file_lock
from app.lantern_jobs import LanternJobs, codex_environment, validate_result
from app.lantern_store import LanternStore


ACTOR = "jianghong"
SOURCE_CONFIG = {"wechat_groups": ["虚构彩灯测试群"], "email": None}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class LanternJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="lantern-job-synthetic-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.store = LanternStore(self.root)
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET must_change_password=0")
        self.project = self.store.save_project(
            {"name": "虚构彩灯项目", "notes": "合成测试台账", "products": []}, ACTOR)
        self.shipment = self.store.save_shipment({"customer": "虚构客户", "order_no": "SYN-ORDER-2"}, ACTOR)
        self.messages = [{"ref": "wx:1", "time": "2026-10-13T09:00:00+08:00",
                          "sender": "虚构测试人员", "source": "虚构彩灯测试群",
                          "text": "虚构彩灯项目丝架已完成；SYN-ORDER-2 出货信息待核对，请人工核对。"}]
        self.result = {"findings": [{"kind": "project", "record_id": self.project["id"],
                                     "summary": "原消息提到丝架完成，待人工复核。",
                                     "evidence_refs": ["wx:1"]}],
                       "limitations": ["全部为虚构测试数据。"]}
        self.jobs = LanternJobs(self.root)
        self.state_path = self.root / "private" / "lantern_jobs" / "state.json"
        self.lock_path = self.state_path.parent / "start.lock"
        self.codex_path = self.root / "synthetic-tools" / "codex.exe"
        self.source_patch = patch("app.lantern_jobs.load_sources", return_value=SOURCE_CONFIG)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.executable_patch = patch("app.lantern_jobs.find_codex", return_value=self.codex_path)
        self.executable_patch.start()
        self.addCleanup(self.executable_patch.stop)
        self.options_patch = patch("app.lantern_jobs.codex_options", return_value=["--ignore-user-config"])
        self.options_patch.start()
        self.addCleanup(self.options_patch.stop)
        # 阻止测试继承真实本机认证/桌面任务环境；仅验证函数本身时单独覆盖。
        self.environment_patch = patch("app.lantern_jobs.codex_environment", return_value={"PATH": "synthetic"})
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

    def start_job(self, scope="all"):
        with patch("app.lantern_jobs.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"", stderr=b"")), \
                patch("app.lantern_jobs.subprocess.Popen", return_value=SimpleNamespace(pid=os.getpid())):
            return self.jobs.start(ACTOR, scope=scope)

    def test_email_scope_sends_only_mail_body_to_codex_and_preserves_manual_state(self):
        email = {"account": "synthetic@example.test"}
        messages = [{"ref": "email:synthetic:17", "time": "2026-10-13T09:00:00+08:00", "sender": "synthetic@example.test",
                     "source": "邮箱:synthetic@example.test/INBOX", "text": "主题：虚构交期\n虚构彩灯项目预计10月20日完成，需确认。"}]
        result = {"findings": [{"kind": "project", "record_id": self.project["id"], "summary": "邮件预计10月20日完成，需确认。", "evidence_refs": [messages[0]["ref"]]}], "limitations": []}
        prompt = []
        def fake_codex(args, **kwargs):
            prompt.append(kwargs["input"].decode("utf-8"))
            write_json(Path(args[args.index("--output-last-message") + 1]), result)
            return SimpleNamespace(returncode=0)
        before = self.store.snapshot()["projects"]
        with patch("app.lantern_jobs.load_sources", return_value={"schema": 1, "wechat_groups": ["禁止采集的虚构微信群"], "email": email}):
            state = self.start_job("email")
            with patch("app.lantern_jobs.collect_sources", return_value={"messages": messages, "limitations": []}) as collect, patch("app.lantern_jobs.subprocess.run", side_effect=fake_codex):
                self.assertEqual(self.jobs.run(state["id"], ACTOR), 0)
        self.assertEqual(collect.call_args.args[2], {"schema": 1, "wechat_groups": [], "email": email})
        self.assertIn("虚构交期", prompt[0])
        self.assertNotIn("禁止采集的虚构微信群", prompt[0])
        self.assertNotIn('"password"', prompt[0])
        self.assertEqual(self.store.snapshot()["projects"], before)
        self.assertEqual(self.jobs.status()["status"], "succeeded")

    def run_with_result(self, *, result=None, write_last_message=True, returncode=0, stdout=None, events=None):
        calls = []
        result = self.result if result is None else result

        def fake_codex(args, **kwargs):
            calls.append((args, kwargs))
            if write_last_message:
                target = Path(args[args.index("--output-last-message") + 1])
                write_json(target, result)
            if events is not None:
                kwargs["stdout"].write(events)
            return SimpleNamespace(returncode=returncode,
                                   stdout=stdout if stdout is not None else b"synthetic stdout is not a result",
                                   stderr=b"")

        state = self.start_job()
        with patch("app.lantern_jobs.collect_sources", return_value={"messages": copy.deepcopy(self.messages), "limitations": ["虚构来源限制"]}) as collect, \
                patch("app.lantern_jobs.subprocess.run", side_effect=fake_codex):
            returned = self.jobs.run(state["id"], ACTOR)
        return state, returned, calls, collect

    def test_validate_real_project_and_shipment_references(self):
        result = copy.deepcopy(self.result)
        result["findings"].append({"kind": "shipment", "record_id": self.shipment["id"],
                                   "summary": "虚构出货信息待核对。", "evidence_refs": ["wx:1"]})
        validate_result(result, self.messages, self.store.snapshot())

    def test_validate_rejects_forged_reference(self):
        result = copy.deepcopy(self.result)
        result["findings"][0]["evidence_refs"] = ["wx:not-collected"]
        with self.assertRaises(ValueError):
            validate_result(result, self.messages, self.store.snapshot())

    def test_validate_rejects_record_outside_current_ledger(self):
        for record_id in ("f" * 32, self.shipment["id"]):
            with self.subTest(record_id=record_id):
                result = copy.deepcopy(self.result)
                result["findings"][0]["record_id"] = record_id
                with self.assertRaises(ValueError):
                    validate_result(result, self.messages, self.store.snapshot())

    def test_validate_rejects_guessed_record_link_without_identifier(self):
        messages = copy.deepcopy(self.messages)
        messages[0]["text"] = "好的，收到了，安排一下。"
        with self.assertRaises(ValueError):
            validate_result(self.result, messages, self.store.snapshot())

    def test_order_number_prefix_does_not_identify_another_shipment(self):
        result = copy.deepcopy(self.result)
        result["findings"][0].update(kind="shipment", record_id=self.shipment["id"])
        messages = copy.deepcopy(self.messages)
        messages[0]["text"] = "SYN-ORDER-20 的出货已安排，请复核。"
        with self.assertRaises(ValueError):
            validate_result(result, messages, self.store.snapshot())

    def test_duplicate_project_name_requires_unique_order_marker_or_general(self):
        self.store.save_project({"name": self.project["name"], "products": []}, ACTOR)
        with self.assertRaises(ValueError):
            validate_result(self.result, self.messages, self.store.snapshot())

    def test_validate_general_requires_empty_record_id(self):
        result = copy.deepcopy(self.result)
        result["findings"][0]["kind"] = "general"
        with self.assertRaises(ValueError):
            validate_result(result, self.messages, self.store.snapshot())
        result["findings"][0]["record_id"] = ""
        validate_result(result, self.messages, self.store.snapshot())

    def test_validate_requires_nonempty_references(self):
        for references in ([], [""], "wx:1", None):
            with self.subTest(references=references):
                result = copy.deepcopy(self.result)
                result["findings"][0]["evidence_refs"] = references
                with self.assertRaises(ValueError):
                    validate_result(result, self.messages, self.store.snapshot())

    def test_validate_rejects_overlong_summary(self):
        result = copy.deepcopy(self.result)
        result["findings"][0]["summary"] = "测" * 4001
        with self.assertRaises(ValueError):
            validate_result(result, self.messages, self.store.snapshot())

    def test_validate_rejects_unknown_kind_and_model_write_fields(self):
        for change in ({"kind": "complete_project"}, {"status": "已完成"}):
            with self.subTest(change=change):
                result = copy.deepcopy(self.result)
                result["findings"][0].update(change)
                with self.assertRaises(ValueError):
                    validate_result(result, self.messages, self.store.snapshot())

    def test_unconfigured_sources_never_start_codex(self):
        with patch("app.lantern_jobs.load_sources", side_effect=ValueError("尚未配置彩灯来源")), \
                patch("app.lantern_jobs.find_codex") as find, \
                patch("app.lantern_jobs.subprocess.run") as run, \
                patch("app.lantern_jobs.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "来源"):
                self.jobs.start(ACTOR)
        find.assert_not_called()
        run.assert_not_called()
        spawn.assert_not_called()

    def test_login_failure_never_starts_worker(self):
        with patch("app.lantern_jobs.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b"")) as login, \
                patch("app.lantern_jobs.subprocess.Popen") as spawn:
            with self.assertRaises(ValueError):
                self.jobs.start(ACTOR)
        self.assertEqual([str(arg) for arg in login.call_args.args[0]],
                         [str(self.codex_path), "login", "status"])
        spawn.assert_not_called()

    def test_empty_source_configuration_cannot_fake_start_success(self):
        with patch("app.lantern_jobs.load_sources", return_value={"wechat_groups": [], "email": None}), \
                patch("app.lantern_jobs.subprocess.run") as login, \
                patch("app.lantern_jobs.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "来源"):
                self.jobs.start(ACTOR)
        login.assert_not_called()
        spawn.assert_not_called()

    def test_login_check_errors_never_start_worker(self):
        for error in (OSError("synthetic unavailable"), subprocess.TimeoutExpired("synthetic codex", 15)):
            with self.subTest(error=type(error).__name__), \
                    patch("app.lantern_jobs.subprocess.run", side_effect=error), \
                    patch("app.lantern_jobs.subprocess.Popen") as spawn:
                with self.assertRaises(ValueError):
                    self.jobs.start(ACTOR)
                spawn.assert_not_called()

    def test_start_uses_fixed_worker_command(self):
        with patch("app.lantern_jobs.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"", stderr=b"")), \
                patch("app.lantern_jobs.subprocess.Popen", return_value=SimpleNamespace(pid=os.getpid())) as spawn:
            state = self.jobs.start(ACTOR)
        args = [str(arg) for arg in spawn.call_args.args[0]]
        self.assertEqual(args, [sys.executable, "-X", "utf8", "-B", "-m", "app.lantern_jobs",
                                "--run", state["id"], "--workspace", str(self.root), "--actor", ACTOR])
        self.assertEqual(Path(spawn.call_args.kwargs["cwd"]), self.root)
        self.assertFalse(spawn.call_args.kwargs.get("shell", False))
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["stage"], "starting")

    def test_duplicate_start_does_not_launch_second_worker(self):
        self.start_job()
        with patch("app.lantern_jobs.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"", stderr=b"")), \
                patch("app.lantern_jobs.subprocess.Popen") as spawn:
            with self.assertRaises(ValueError):
                LanternJobs(self.root).start(ACTOR)
        spawn.assert_not_called()

    def test_cross_instance_start_lock_blocks_concurrent_launch(self):
        with file_lock(self.lock_path), \
                patch("app.lantern_jobs.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"", stderr=b"")), \
                patch("app.lantern_jobs.subprocess.Popen") as spawn:
            with self.assertRaises(ValueError):
                self.jobs.start(ACTOR)
        spawn.assert_not_called()

    def test_failed_spawn_marks_failed_and_releases_start_lock(self):
        with patch("app.lantern_jobs.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"", stderr=b"")), \
                patch("app.lantern_jobs.subprocess.Popen", side_effect=OSError("synthetic worker failure")):
            with self.assertRaises(ValueError):
                self.jobs.start(ACTOR)
        self.assertEqual(self.jobs.status()["status"], "failed")
        with file_lock(self.lock_path):
            pass

    def test_dead_worker_recovers_to_failed(self):
        job_id = "2" * 32
        write_json(self.state_path, {"id": job_id, "status": "running", "stage": "analyzing", "pid": 987654,
                                     "started_at": "2026-01-01T00:00:00+08:00", "requested_by": ACTOR})
        with patch("app.lantern_jobs.alive", return_value=False):
            status = self.jobs.status()
        self.assertEqual(status["status"], "failed")
        self.assertIn("finished_at", status)
        self.assertEqual(json.loads(self.state_path.read_text("utf-8"))["status"], "failed")

    def test_worker_uses_last_message_file_and_preserves_manual_ledger(self):
        baseline = self.store.snapshot()
        # stdout 中存在另一份合法 JSON，也必须只采用最后消息文件。
        stdout_result = copy.deepcopy(self.result)
        stdout_result["findings"][0]["summary"] = "不应导入的 stdout 伪结果"
        state, _, calls, collect = self.run_with_result(stdout=json.dumps(stdout_result).encode("utf-8"))
        self.assertEqual(self.jobs.status()["status"], "succeeded")
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        args = [str(arg) for arg in args]
        self.assertEqual(args[0], str(self.codex_path))
        self.assertIn("exec", args)
        self.assertIn("--output-last-message", args)
        for option in ("--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json", "--output-schema"):
            self.assertIn(option, args)
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertEqual(args[-1], "-")
        self.assertIn('web_search="disabled"', args)
        for feature in ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent"):
            self.assertEqual(args[args.index(feature) - 1], "--disable")
        self.assertFalse(kwargs.get("shell", False))
        self.assertIn("input", kwargs)
        prompt = kwargs["input"]
        if isinstance(prompt, bytes):
            prompt = prompt.decode("utf-8")
        self.assertIn(self.messages[0]["text"], prompt)
        self.assertIn(self.project["id"], prompt)
        self.assertEqual(kwargs["env"], {"PATH": "synthetic"})
        collect.assert_called_once()
        self.assertEqual(Path(collect.call_args.args[0]), self.root)
        self.assertEqual(collect.call_args.args[2], SOURCE_CONFIG)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["projects"], baseline["projects"])
        self.assertEqual(snapshot["shipments"], baseline["shipments"])
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertIn(self.result["findings"][0]["summary"], serialized)
        self.assertNotIn(stdout_result["findings"][0]["summary"], serialized)

    def test_stdout_result_without_last_message_file_is_failure(self):
        baseline = self.store.snapshot()
        self.run_with_result(write_last_message=False, stdout=json.dumps(self.result).encode("utf-8"))
        self.assertEqual(self.jobs.status()["status"], "failed")
        self.assertEqual(self.store.snapshot(), baseline)

    def test_collection_failure_keeps_last_successful_findings(self):
        self.run_with_result()
        baseline = self.store.snapshot()
        state = self.start_job()
        with patch("app.lantern_jobs.collect_sources", side_effect=ValueError("虚构来源采集失败")), \
                patch("app.lantern_jobs.subprocess.run") as run:
            self.jobs.run(state["id"], ACTOR)
        run.assert_not_called()
        self.assertEqual(self.jobs.status()["status"], "failed")
        self.assertEqual(self.store.snapshot(), baseline)

    def test_model_failure_keeps_last_successful_findings(self):
        self.run_with_result()
        baseline = self.store.snapshot()
        self.run_with_result(returncode=2)
        self.assertEqual(self.jobs.status()["status"], "failed")
        self.assertEqual(self.store.snapshot(), baseline)

    def test_invalid_model_result_keeps_last_successful_findings(self):
        self.run_with_result()
        baseline = self.store.snapshot()
        invalid = copy.deepcopy(self.result)
        invalid["findings"][0]["evidence_refs"] = ["wx:forged"]
        self.run_with_result(result=invalid)
        self.assertEqual(self.jobs.status()["status"], "failed")
        self.assertEqual(self.store.snapshot(), baseline)

    def test_unexpected_tool_event_rejects_result_and_keeps_previous_findings(self):
        self.run_with_result()
        baseline = self.store.snapshot()
        tool_event = {"type": "item.completed", "item": {"type": "command_execution", "command": "synthetic"}}
        self.run_with_result(events=json.dumps(tool_event).encode("utf-8") + b"\n")
        self.assertEqual(self.jobs.status()["status"], "failed")
        self.assertIn("工具", self.jobs.status()["message"])
        self.assertEqual(self.store.snapshot(), baseline)


class CodexEnvironmentTests(unittest.TestCase):
    def test_cli_environment_drops_desktop_task_and_pipe_variables(self):
        environment = {"PATH": "synthetic-path", "CODEX_HOME": "synthetic-home",
                       "CODEX_THREAD_ID": "synthetic-task", "CODEX_TOOL_PIPE": "synthetic-pipe",
                       "CODEX_SANDBOX_NETWORK_DISABLED": "1", "OTHER_TEST_SETTING": "retained"}
        with patch.dict(os.environ, environment, clear=True):
            result = codex_environment()
        self.assertEqual(result, {"PATH": "synthetic-path", "CODEX_HOME": "synthetic-home",
                                  "OTHER_TEST_SETTING": "retained"})


if __name__ == "__main__":
    unittest.main()
