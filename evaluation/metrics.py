"""
Metrics (spec section 38). Deliberately NOT just overall accuracy — the spec
is explicit that hiding per-check breakdowns behind one aggregate number is
unacceptable for a compliance system. FAIL is treated as the "positive"
class for precision/recall because a missed real defect (false PASS) is the
costliest failure mode for this product; UNCERTAIN is tracked separately
since it's a deliberate non-answer, not a wrong answer.
"""
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class CheckMetrics:
    check_id: str
    total: int = 0
    correct: int = 0
    uncertain_count: int = 0
    confusion: dict = field(default_factory=lambda: defaultdict(int))  # (truth, pred) -> count
    false_fails: int = 0     # predicted FAIL, truth PASS — costs the operator rework
    false_passes: int = 0    # predicted PASS, truth FAIL — costs the business a real defect shipped
    tp: int = 0  # predicted FAIL, truth FAIL
    fp: int = 0  # predicted FAIL, truth PASS
    fn: int = 0  # predicted PASS, truth FAIL

    @property
    def accuracy(self):
        return self.correct / self.total if self.total else None

    @property
    def uncertain_rate(self):
        return self.uncertain_count / self.total if self.total else None

    @property
    def coverage(self):
        return (self.total - self.uncertain_count) / self.total if self.total else None

    @property
    def precision(self):
        denom = self.tp + self.fp
        return self.tp / denom if denom else None

    @property
    def recall(self):
        denom = self.tp + self.fn
        return self.tp / denom if denom else None

    @property
    def f1(self):
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)

    def to_dict(self):
        return {
            "check_id": self.check_id, "total": self.total, "accuracy": self.accuracy,
            "uncertain_rate": self.uncertain_rate, "coverage": self.coverage,
            "precision_fail": self.precision, "recall_fail": self.recall, "f1_fail": self.f1,
            "false_fail_count": self.false_fails, "false_pass_count": self.false_passes,
            "confusion_matrix": {f"truth={t}/pred={p}": c for (t, p), c in self.confusion.items()},
        }


def score_run(predictions: dict, ground_truth: dict) -> dict:
    """
    predictions:   {unit_id: {check_id: verdict_str}}
    ground_truth:  {unit_id: {check_id: verdict_str}}
    Returns {check_id: CheckMetrics.to_dict()} plus an "_overall" summary.
    """
    per_check: dict[str, CheckMetrics] = {}

    for unit_id, truth_checks in ground_truth.items():
        pred_checks = predictions.get(unit_id, {})
        for check_id, truth_verdict in truth_checks.items():
            pred_verdict = pred_checks.get(check_id)
            if pred_verdict is None:
                continue  # unit wasn't processed / check didn't fire — excluded, not silently scored
            m = per_check.setdefault(check_id, CheckMetrics(check_id=check_id))
            m.total += 1
            m.confusion[(truth_verdict, pred_verdict)] += 1
            if pred_verdict == truth_verdict:
                m.correct += 1
            if pred_verdict == "UNCERTAIN":
                m.uncertain_count += 1
            if pred_verdict == "FAIL" and truth_verdict == "FAIL":
                m.tp += 1
            elif pred_verdict == "FAIL" and truth_verdict == "PASS":
                m.fp += 1
                m.false_fails += 1
            elif pred_verdict == "PASS" and truth_verdict == "FAIL":
                m.fn += 1
                m.false_passes += 1

    result = {cid: m.to_dict() for cid, m in per_check.items()}
    all_totals = sum(m.total for m in per_check.values())
    all_correct = sum(m.correct for m in per_check.values())
    all_uncertain = sum(m.uncertain_count for m in per_check.values())
    result["_overall"] = {
        "total_check_evaluations": all_totals,
        "accuracy_across_all_checks": (all_correct / all_totals) if all_totals else None,
        "uncertain_rate_across_all_checks": (all_uncertain / all_totals) if all_totals else None,
        "total_false_passes": sum(m.false_passes for m in per_check.values()),
        "total_false_fails": sum(m.false_fails for m in per_check.values()),
    }
    return result
