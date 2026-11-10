"""Explicit current-view selections for report-content tests predating Working roles."""

from cbs_transcriber import research_reports


def selected_view_report(store, project_id, selections=None, *args, **kwargs):
    if selections is None:
        selections = store.all(
            "SELECT r.id recording_id,t.id result_id,"
            "(SELECT id FROM result_versions v WHERE v.result_id=t.id "
            "ORDER BY sequence DESC LIMIT 1) version_id FROM recordings r "
            "JOIN transcript_results t ON t.id=r.active_transcript_id "
            "WHERE r.project_id=? ORDER BY r.created,r.id",
            (project_id,),
        )
    return research_reports.project_report(store, project_id, selections, *args, **kwargs)
