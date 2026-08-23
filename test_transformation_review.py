from transformation_review import build_transformation_review


def _issue(position, check, category, severity="CRITICAL"):
    return {
        "Position ID": position,
        "Check ID": check,
        "Check Category": category,
        "Severity": severity,
    }


def test_review_prioritises_critical_findings_and_deduplicates_positions():
    issues = [
        _issue("P1", "CHK-02", "Hierarchy Alignment"),
        _issue("P1", "CHK-03", "Hierarchy Alignment"),
        _issue("P2", "CHK-05", "Cost Centre Alignment", "HIGH"),
    ]

    review = build_transformation_review(issues, total_positions=10)

    assert review["priority"] == "immediate_control_review"
    assert review["impacted_positions"] == 2
    assert review["impacted_position_rate"] == 0.2
    assert review["finding_counts"] == {"total": 3, "critical": 2, "high": 1}
    assert review["human_control"]["writeback_automatic"] is False
    assert review["root_cause_clusters"][0]["check_ids"] == ["CHK-02", "CHK-03"]


def test_clean_review_recommends_monitoring():
    review = build_transformation_review([], total_positions=20)

    assert review["priority"] == "monitor"
    assert review["recommended_actions"][0]["approval_required"] is False
    assert review["root_cause_clusters"] == []


def test_zero_total_does_not_divide_by_zero():
    review = build_transformation_review([_issue("P1", "CHK-08", "Job Code", "HIGH")], 0)

    assert review["impacted_position_rate"] == 0.0
    assert review["priority"] == "planned_remediation"
