from __future__ import annotations

from io import BytesIO

import pandas as pd

from .io import InputBundle
from .models import ScheduleParameters, ScheduleResult


def schedule_excel_bytes(
    bundle: InputBundle,
    result: ScheduleResult,
    parameters: ScheduleParameters,
) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        summary = pd.DataFrame(
            [
                ["solver_status", result.status],
                ["assigned_sessions", len(result.assignments)],
                ["unassigned_sessions", len(result.unassigned)],
                ["average_preference", result.average_preference],
                ["wall_time_seconds", result.wall_time_seconds],
                ["max_teaching_hours_per_day", parameters.max_teaching_hours_per_day],
                ["max_study_hours_per_day", parameters.max_study_hours_per_day],
                ["max_course_hours_per_day", parameters.max_course_hours_per_day],
                [
                    "max_courses_per_teacher_per_day",
                    parameters.max_courses_per_teacher_per_day,
                ],
                ["avoid_consecutive_days", parameters.avoid_consecutive_days],
                ["strict_zero_preference", parameters.strict_zero_preference],
            ],
            columns=["metric", "value"],
        )
        summary.to_excel(writer, sheet_name="summary", index=False)
        result.assignments.drop(
            columns=["day_index", "start_slot"], errors="ignore"
        ).to_excel(writer, sheet_name="schedule", index=False)
        result.unassigned.to_excel(writer, sheet_name="unassigned", index=False)
        pd.DataFrame({"diagnostic": result.diagnostics}).to_excel(
            writer, sheet_name="diagnostics", index=False
        )

        workbook = writer.book
        header = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#171536",
                "border": 0,
                "align": "center",
                "valign": "vcenter",
            }
        )
        for sheet_name, frame in [
            ("summary", summary),
            ("schedule", result.assignments.drop(columns=["day_index", "start_slot"], errors="ignore")),
            ("unassigned", result.unassigned),
            ("diagnostics", pd.DataFrame({"diagnostic": result.diagnostics})),
        ]:
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, 0)
            worksheet.hide_gridlines(2)
            worksheet.set_row(0, 24, header)
            for column_index, column in enumerate(frame.columns):
                width = min(
                    max(
                        len(str(column)) + 2,
                        frame[column].astype(str).str.len().max() + 2
                        if not frame.empty
                        else 12,
                    ),
                    38,
                )
                worksheet.set_column(column_index, column_index, width)
    return output.getvalue()
