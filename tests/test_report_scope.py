"""Reports tell passage repairs from whole-result enhancement and share the edit vocabulary."""

from cbs_transcriber import history, research_reports


def test_repairs_and_whole_results_have_distinct_scopes_without_passage_ids():
    repair = history.processing_metadata({"kind": "enhancement", "segment_ids": ["3", "7"]})
    single = history.processing_metadata({"kind": "enhancement", "segment_id": "3"})
    whole = history.processing_metadata({"kind": "enhancement"})
    assert repair["scope"] == "passage_repairs" and repair["passage_count"] == 2
    assert single == {"scope": "passage_repair", "passage_count": 1}
    assert whole == {"scope": "whole_result"}
    reported = research_reports._processing(repair)
    assert reported["scope"] == "passage_repairs" and reported["passage_count"] == 2
    assert "segment_ids" not in reported
    assert research_reports._processing({"scope": "anything", "passage_count": 10**6}) == {}


def test_every_recorded_manual_correction_counts_as_an_edit_in_reports():
    assert research_reports.EDIT_ACTIONS is history.EDIT_ACTIONS
    assert "manual_save" in research_reports.EDIT_ACTIONS
    assert history.EDIT_ACTIONS <= research_reports.ACTIONS
