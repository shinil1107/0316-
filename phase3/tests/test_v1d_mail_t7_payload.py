"""V1-D — R11B EOD digest folds the T7 prefix-stage payload.

After V1-A and V1-B the operator can drive T7 → R11A from a single
click. V1-D extends the existing R11B post-trade mail (the only
operator-facing summary V1 emits, T7's own email is suppressed) so
the digest carries:

  * T7 outcome block (ok / rc / recs_written / duration / error)
  * a tail of ``recommendations.csv`` for at-a-glance review
  * ``recommendations.csv`` as an attachment
  * a subject prefix that distinguishes V1 from R11A v0 runs

Behaviour for runs that did NOT pass a ``t7_payload`` (i.e. v0
Skip-T7 runs) MUST be byte-identical to R11B before V1 — see the
parity test that compares both bodies under the same fixtures.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import smtp_mailer as sm


def _write_recs(rd: Path, rows: int = 5) -> None:
    rd.mkdir(parents=True, exist_ok=True)
    header = "RecRowId,Action,Ticker,Shares,Price,EquityWeight\n"
    lines = [
        f"{i},BUY_NEW,T{i},{i + 1},{10.0 + i:.4f},{0.1 * (i + 1):.4f}\n"
        for i in range(rows)
    ]
    (rd / "recommendations.csv").write_text(header + "".join(lines),
                                              encoding="utf-8")


def _stage_outcomes() -> List[Dict[str, Any]]:
    return [
        {"key": "generate_intents", "rc": 0, "duration_sec": 5.0,
         "halt_reason": None, "skipped": False},
        {"key": "dry_run", "rc": 0, "duration_sec": 1.5,
         "halt_reason": None, "skipped": False},
        {"key": "paper_submit", "rc": 0, "duration_sec": 8.0,
         "halt_reason": None, "skipped": False},
        {"key": "t10_apply", "rc": 0, "duration_sec": 2.5,
         "halt_reason": None, "skipped": False},
    ]


def _t7_payload(ok: bool = True, recs: int = 5) -> Dict[str, Any]:
    return {
        "ok": ok, "rc": 0 if ok else 2,
        "run_id": "20260526_223714_daily",
        "recommendations_count": recs,
        "duration_sec": 73.4,
        "error": "" if ok else "synthetic failure",
        "stdout_tail": "(tail)", "stderr_tail": "",
        "suppressed_mail": True,
    }


class TestSubjectLine(unittest.TestCase):

    def test_v1_subject_marks_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper",
                overall_rc=0, halt_reason=None, duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=_t7_payload(),
            )
            self.assertIn("[Autotrade paper V1]", p.subject)

    def test_skip_t7_subject_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            rd.mkdir(parents=True)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper",
                overall_rc=0, halt_reason=None, duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=None,
            )
            self.assertIn("[Autotrade paper]", p.subject)
            self.assertNotIn("V1", p.subject)


class TestBodyT7Block(unittest.TestCase):

    def test_body_includes_t7_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, rows=3)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=0, halt_reason=None,
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=_t7_payload(recs=3),
            )
            body = p.body_text
            self.assertIn("T7 (recommendation generation)", body)
            self.assertIn("run_id       : 20260526_223714_daily", body)
            self.assertIn("recs_written : 3", body)
            self.assertIn("duration_sec : 73.4", body)
            self.assertIn("pipeline     : V1 (T7 → R11A)", body)
            self.assertIn("SUPPRESSED", body)

    def test_body_lists_recommendation_rows(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, rows=4)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=0, halt_reason=None,
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=_t7_payload(recs=4),
            )
            body = p.body_text
            self.assertIn("Recommendations (top 4 of 4)", body)
            for ticker in ("T0", "T1", "T2", "T3"):
                self.assertIn(ticker, body)

    def test_body_truncates_long_recs_with_overflow_note(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, rows=40)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=0, halt_reason=None,
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=_t7_payload(recs=40),
            )
            body = p.body_text
            self.assertIn("Recommendations (top 25 of 40)", body)
            self.assertIn("(15 more", body)

    def test_body_shows_t7_error_when_failed(self):
        """V1 should still mail on T7 halt (operator must know)."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            rd.mkdir(parents=True)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=2,
                halt_reason="T7 failed",
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=_t7_payload(ok=False),
            )
            body = p.body_text
            self.assertIn("synthetic failure", body)
            self.assertIn("status       : halted", body)

    def test_body_handles_missing_recommendations_csv(self):
        """Mail must still compose if T7 created the dir but recs
        write failed — operator needs SOME notification."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            rd.mkdir(parents=True)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=0, halt_reason=None,
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=_t7_payload(),
            )
            self.assertIsNotNone(p.subject)
            self.assertNotIn("Recommendations (top", p.body_text)


class TestAttachments(unittest.TestCase):

    def test_v1_attaches_recommendations_csv(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, rows=3)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=0, halt_reason=None,
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=_t7_payload(),
            )
            names = [a.filename for a in p.attachments]
            self.assertIn("recommendations.csv", names)

    def test_skip_t7_does_not_attach_recommendations_csv(self):
        """When t7_payload is None we are in the v0 Skip-T7 path —
        keep the historical attachment set unchanged."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=0, halt_reason=None,
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=None,
            )
            names = [a.filename for a in p.attachments]
            self.assertNotIn("recommendations.csv", names)


class TestSkipT7Parity(unittest.TestCase):
    """When the caller does NOT pass a ``t7_payload`` the V1 changes
    must be invisible — body & subject identical to pre-V1."""

    def test_body_does_not_mention_t7(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd)
            p = sm.compose_run_summary_mail(
                run_dir=rd, run_id="rid",
                profile="paper", overall_rc=0, halt_reason=None,
                duration_sec=17.0,
                stage_outcomes=_stage_outcomes(),
                t7_payload=None,
            )
            self.assertNotIn("T7 (recommendation generation)", p.body_text)
            self.assertNotIn("pipeline     : V1", p.body_text)
            self.assertNotIn("Recommendations (top", p.body_text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
