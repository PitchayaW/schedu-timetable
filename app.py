from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from scheduler.export import schedule_excel_bytes
from scheduler.io import (
    CANONICAL_SHEETS,
    InputBundle,
    load_main_workbook,
    load_preferences,
)
from scheduler.models import ScheduleParameters, ScheduleResult
from scheduler.runner import run_solver_isolated
from scheduler.time_utils import DAY_THAI, TIMES, split_items

APP_DIR = Path(__file__).resolve().parent
SAMPLE_FILE = APP_DIR / "sample_data" / "SchEDU_input_68_1.xlsx"

st.set_page_config(
    page_title="SchEDU · University Timetable",
    page_icon="◫",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ink: #171536;
          --muted: #68657b;
          --line: #e8e6ee;
          --paper: #fbfafc;
          --accent: #ff6a3d;
          --violet: #5a4ff3;
          --mint: #cff7df;
        }
        .stApp { background: var(--paper); color: var(--ink); }
        [data-testid="stSidebar"] { background: #171536; }
        [data-testid="stSidebar"] * { color: #f8f7fb; }
        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] [data-baseweb="select"] * { color: #171536; }
        .block-container { max-width: 1480px; padding-top: 1.6rem; }
        .hero {
          padding: 1.7rem 1.9rem; border-radius: 22px;
          background:
            radial-gradient(circle at 88% 12%, rgba(255,255,255,.18), transparent 22%),
            linear-gradient(120deg, #171536 0%, #32286c 58%, #5a4ff3 100%);
          color: white; margin-bottom: 1rem; overflow: hidden;
        }
        .eyebrow { font-size: .78rem; letter-spacing: .16em; font-weight: 750; opacity: .72; }
        .hero h1 { font-size: clamp(2rem, 4vw, 3.35rem); line-height: .98; margin: .5rem 0 .8rem; }
        .hero p { max-width: 760px; margin: 0; color: #dedaf9; font-size: 1rem; }
        .step {
          display: inline-flex; align-items: center; gap: .5rem; padding: .38rem .7rem;
          border: 1px solid var(--line); background: white; border-radius: 999px;
          color: var(--ink); font-size: .82rem; margin-bottom: .5rem;
        }
        .step b { background: var(--ink); color: white; border-radius: 50%; width: 1.35rem;
          height: 1.35rem; display: grid; place-items: center; }
        div[data-testid="stMetric"] {
          background: white; border: 1px solid var(--line); border-radius: 16px;
          padding: .85rem 1rem;
        }
        div[data-testid="stMetric"] label { color: var(--muted); }
        .status-ok, .status-warn, .status-bad {
          padding: .8rem 1rem; border-radius: 12px; margin: .35rem 0;
        }
        .status-ok { background: #e9f9ef; color: #176a3a; }
        .status-warn { background: #fff4df; color: #895b08; }
        .status-bad { background: #ffe9e7; color: #9f2e20; }
        .stButton > button[kind="primary"] {
          background: var(--accent); border-color: var(--accent); border-radius: 12px;
          min-height: 3rem; font-weight: 750;
        }
        .stDownloadButton > button { border-radius: 12px; }
        .table-wrap {
          overflow: auto; max-height: 430px; border: 1px solid var(--line);
          border-radius: 14px; background: white;
        }
        .table-wrap table { border-collapse: collapse; width: 100%; font-size: .83rem; }
        .table-wrap th {
          position: sticky; top: 0; background: #171536; color: white;
          padding: .62rem .7rem; text-align: left; white-space: nowrap;
        }
        .table-wrap td {
          border-bottom: 1px solid var(--line); padding: .55rem .7rem;
          white-space: pre-line; vertical-align: top;
        }
        .table-wrap tr:nth-child(even) td { background: #f7f6fa; }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def read_main(data: bytes) -> InputBundle:
    return load_main_workbook(data)


@st.cache_data(show_spinner=False)
def read_preferences(data: bytes) -> dict[str, dict[tuple[int, int], float]]:
    return load_preferences(data)


def apply_preference_input(
    bundle: InputBundle,
    preference_data: bytes | None,
) -> InputBundle:
    bundle.preferences = read_preferences(preference_data) if preference_data else {}
    return bundle


def html_table(frame: pd.DataFrame, *, show_index: bool = False) -> None:
    safe_frame = frame.fillna("").astype(str)
    st.markdown(
        '<div class="table-wrap">'
        + safe_frame.to_html(index=show_index, escape=True, border=0)
        + "</div>",
        unsafe_allow_html=True,
    )


def data_overview(bundle: InputBundle) -> None:
    metrics = [
        ("วิชาทั้งหมด", len(bundle.all_courses)),
        ("วิชาที่สาขาจัด", len(bundle.department_courses)),
        ("รายการวิชาที่ นศ. เรียน", len(bundle.student_courses)),
        ("ห้องเรียน", len(bundle.rooms)),
        ("กลุ่มนักศึกษา", len(bundle.student_groups)),
    ]
    columns = st.columns(5)
    for column, (label, value) in zip(columns, metrics):
        column.metric(label, f"{value:,}")

    for message in bundle.messages:
        css = {"success": "status-ok", "warning": "status-warn", "error": "status-bad"}[
            message.level
        ]
        st.markdown(
            f'<div class="{css}"><b>{message.sheet}</b> · {message.message}</div>',
            unsafe_allow_html=True,
        )

    labels = {
        "all_courses": "รายวิชาที่เปิดสอนทั้งหมด",
        "department_courses": "รายวิชาที่สาขาเป็นผู้จัด",
        "student_courses": "รายวิชาที่แต่ละกลุ่มต้องเรียน",
        "rooms": "ห้องเรียน",
        "student_groups": "กลุ่มนักศึกษา",
    }
    with st.expander("ตรวจข้อมูลทั้ง 5 ชีท"):
        tabs = st.tabs([labels[name] for name in CANONICAL_SHEETS])
        for tab, name in zip(tabs, CANONICAL_SHEETS):
            with tab:
                frame = getattr(bundle, name).drop(
                    columns=["_course_key"], errors="ignore"
                )
                html_table(frame)


def input_data_guide() -> None:
    with st.expander("คำอธิบาย Input ทั้ง 5 ชีท"):
        st.markdown(
            """
            **`all_courses` — รายวิชาที่เปิดสอนทั้งหมด**  
            ใช้ตรวจเวลาที่ถูกล็อกไว้แล้วของอาจารย์และกลุ่มนักศึกษา ครอบคลุมทั้ง
            วิชาที่สาขาจัด วิชาบริการ และวิชาจากหน่วยงานอื่น

            **`department_courses` — รายวิชาที่สาขาต้องการจัดตาราง**  
            ระบุรหัสวิชา กลุ่มเรียน ผู้สอน จำนวนผู้เรียน ประเภทห้อง และกลุ่มนักศึกษา
            ทุกสาขา/ชั้นปีที่จะเรียนวิชานั้นใน `student_groups` โดยคั่นหลายกลุ่มด้วย `/`
            รายชื่อกลุ่มเหล่านี้ต้องเป็นผลที่สรุปแล้วจากการวิเคราะห์การสับหลีก

            **`session_hours` ใน `department_courses`**  
            กำหนดจำนวนครั้งที่เรียนต่อสัปดาห์และจำนวนชั่วโมงต่อครั้ง โดยทุกครั้งต้อง
            ยาวเท่ากัน จำนวนค่าคือจำนวนครั้งต่อสัปดาห์ เช่น `1.5,1.5` หมายถึง
            เรียน 2 ครั้ง ครั้งละ 1.5 ชั่วโมง, `2,2` หมายถึง 2 ครั้ง ครั้งละ
            2 ชั่วโมง และ `3` หมายถึง 1 ครั้ง ครั้งละ 3 ชั่วโมง

            **`student_courses` — วิชาที่แต่ละกลุ่มนักศึกษาต้องเรียน**  
            ผู้ใช้ต้องระบุรายวิชาของทุกกลุ่ม/ทุกชั้นปีให้ครบ รวมทั้งวิชานอกสาขาและ
            เวลาที่ล็อกไว้ เพื่อให้ระบบป้องกันตารางชนกันได้

            **`rooms` — ห้องเรียน**  
            ระบุชื่อห้อง ความจุ ประเภทห้อง สิทธิ์สำหรับระดับบัณฑิตศึกษา และช่วงเวลา
            ที่ห้องไม่พร้อมใช้งาน

            **`student_groups` — กลุ่มนักศึกษา**  
            กำหนดรหัสกลุ่ม สาขา ชั้นปี จำนวนนักศึกษา และชั่วโมงเรียนสูงสุดต่อวัน
            รหัส `group_id` ต้องตรงกับที่ใช้ในอีกสองชีท
            """
        )


def timetable_grid(
    assignments: pd.DataFrame, field: str, selected: str
) -> pd.DataFrame:
    grid = pd.DataFrame("", index=DAY_THAI, columns=TIMES)
    for _, row in assignments.iterrows():
        values = split_items(row.get(field))
        if selected not in values:
            continue
        label = f"{row['course_id']}\n{row['room']}"
        start = int(row["start_slot"])
        duration = round(float(row["duration_hours"]) * 2)
        for slot in range(start, min(start + duration, len(TIMES))):
            grid.iloc[int(row["day_index"]), slot] = label
    grid.index.name = "วัน"
    return grid


def entity_values(assignments: pd.DataFrame, field: str) -> list[str]:
    return sorted(
        {
            item
            for value in assignments[field].fillna("")
            for item in split_items(value)
            if item
        }
    )


def render_schedule(
    bundle: InputBundle,
    result: ScheduleResult,
    parameters: ScheduleParameters,
) -> None:
    st.markdown("### ผลการจัดตาราง")
    columns = st.columns(5)
    columns[0].metric("สถานะ", result.status)
    columns[1].metric("จัดได้", f"{len(result.assignments):,} คาบ")
    columns[2].metric("ยังไม่จัด", f"{len(result.unassigned):,} คาบ")
    columns[3].metric("ตัวเลือกตำแหน่ง", f"{result.candidate_count:,}")
    columns[4].metric("Preference เฉลี่ย", f"{result.average_preference:.2f}")
    st.caption(
        "ตัวเลือกตำแหน่ง = ผลรวมตำแหน่งวัน–เวลา–ห้องที่ผ่านข้อจำกัดเบื้องต้น "
        "สำหรับทุกคาบที่ยังไม่ล็อกเวลา ไม่ใช่จำนวนตารางสมบูรณ์ทั้งหมด"
    )

    if result.assignments.empty:
        st.error("ยังไม่มีตารางที่แสดงได้ โปรดดูคำแนะนำในแท็บ Diagnostics")
        if not result.unassigned.empty:
            st.markdown("#### วิเคราะห์คาบที่ยังไม่ได้จัด")
            html_table(
                result.unassigned.rename(
                    columns={
                        "feasible_options": "ตัวเลือกที่ผ่านเบื้องต้น",
                        "blocking_constraints": "ข้อจำกัดที่ขวาง",
                    }
                )
            )
        if not result.option_summary.empty:
            with st.expander("จำนวนตัวเลือกต่อคาบ"):
                html_table(
                    result.option_summary.rename(
                        columns={"feasible_options": "ตัวเลือกที่ผ่านเบื้องต้น"}
                    )
                )
        for message in result.diagnostics:
            st.write(f"• {message}")
    else:
        tab_group, tab_teacher, tab_room, tab_rows, tab_diag = st.tabs(
            ["กลุ่มนักศึกษา", "อาจารย์", "ห้องเรียน", "รายการทั้งหมด", "Diagnostics"]
        )
        for tab, field, label in [
            (tab_group, "student_groups", "เลือกกลุ่มนักศึกษา"),
            (tab_teacher, "teachers", "เลือกอาจารย์"),
            (tab_room, "room", "เลือกห้อง"),
        ]:
            with tab:
                options = entity_values(result.assignments, field)
                if options:
                    selected = st.selectbox(label, options, key=f"view_{field}")
                    grid = timetable_grid(result.assignments, field, selected)
                    html_table(grid, show_index=True)
                else:
                    st.info("ไม่มีข้อมูลสำหรับมุมมองนี้")
        with tab_rows:
            html_table(
                result.assignments.drop(
                    columns=["day_index", "start_slot"], errors="ignore"
                )
            )
            if not result.unassigned.empty:
                st.markdown("#### วิเคราะห์คาบที่ยังไม่ได้จัด")
                html_table(
                    result.unassigned.rename(
                        columns={
                            "feasible_options": "ตัวเลือกที่ผ่านเบื้องต้น",
                            "blocking_constraints": "ข้อจำกัดที่ขวาง",
                        }
                    )
                )
        with tab_diag:
            if not result.option_summary.empty:
                st.markdown("#### จำนวนตัวเลือกต่อคาบ")
                html_table(
                    result.option_summary.rename(
                        columns={"feasible_options": "ตัวเลือกที่ผ่านเบื้องต้น"}
                    )
                )
            if not result.unassigned.empty:
                st.markdown("#### ข้อจำกัดที่ขวางคาบซึ่งยังไม่ได้จัด")
                html_table(
                    result.unassigned.rename(
                        columns={
                            "feasible_options": "ตัวเลือกที่ผ่านเบื้องต้น",
                            "blocking_constraints": "ข้อจำกัดที่ขวาง",
                        }
                    )
                )
            for message in result.diagnostics:
                st.write(f"• {message}")

    export_bytes = schedule_excel_bytes(bundle, result, parameters)
    st.download_button(
        "ดาวน์โหลดผลลัพธ์ Excel",
        data=export_bytes,
        file_name="SchEDU_schedule.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="stretch",
    )


def sidebar_parameters() -> ScheduleParameters:
    st.sidebar.markdown("## พารามิเตอร์")
    st.sidebar.caption("ข้อจำกัดรายวันและพฤติกรรมของตัวแก้ปัญหา")
    max_teach = st.sidebar.number_input(
        "ชั่วโมงสอนสูงสุด / อาจารย์ / วัน",
        min_value=1.0,
        max_value=10.0,
        value=8.0,
        step=0.5,
        help="ภาระสอนรวมสูงสุดของอาจารย์แต่ละคนในหนึ่งวัน รวมคาบที่ล็อกไว้แล้ว",
    )
    st.sidebar.caption("จำกัดภาระสอนรวมของอาจารย์แต่ละคนในหนึ่งวัน")
    max_study = st.sidebar.number_input(
        "ชั่วโมงเรียนสูงสุด / กลุ่ม / วัน",
        min_value=1.0,
        max_value=10.0,
        value=8.0,
        step=0.5,
        help="เพดานชั่วโมงเรียนรวมต่อวันของแต่ละกลุ่มนักศึกษา",
    )
    st.sidebar.caption("จำกัดชั่วโมงเรียนรวมต่อวันของแต่ละกลุ่มนักศึกษา")
    max_course = st.sidebar.number_input(
        "ชั่วโมงสูงสุดของวิชาเดียวกัน / วัน",
        min_value=1.0,
        max_value=8.0,
        value=6.0,
        step=0.5,
        help="เพดานชั่วโมงของรายวิชาเดียวกันที่อนุญาตให้เกิดในวันเดียว",
    )
    st.sidebar.caption("จำกัดชั่วโมงของรายวิชาเดียวกันในวันเดียว")
    max_courses = st.sidebar.number_input(
        "จำนวนวิชาสูงสุด / อาจารย์ / วัน",
        min_value=1,
        max_value=8,
        value=3,
        step=1,
        help="จำนวนรายวิชาที่แตกต่างกันสูงสุดที่อาจารย์หนึ่งคนสอนได้ในหนึ่งวัน",
    )
    st.sidebar.caption("จำกัดจำนวนรายวิชาที่ต่างกันของอาจารย์ในหนึ่งวัน")
    st.sidebar.divider()
    strict_zero = st.sidebar.toggle(
        "ห้ามลงช่วง Preference = 0",
        value=True,
        help="ถือว่าคะแนน 0 คือช่วงที่อาจารย์ไม่พร้อมสอนและห้ามระบบเลือก",
    )
    st.sidebar.caption("คะแนน 0 หมายถึงช่วงที่อาจารย์ไม่พร้อมสอน")
    avoid_consecutive = st.sidebar.toggle(
        "หลีกเลี่ยงเรียนวิชาเดิมวันติดกัน",
        value=True,
        help="ไม่ให้คาบของรายวิชาเดียวกันเกิดในวันต่อเนื่องกัน",
    )
    st.sidebar.caption("กระจายคาบของวิชาเดียวกันไม่ให้ลงวันต่อเนื่อง")
    allow_unassigned = st.sidebar.toggle(
        "ยอมให้บางคาบยังไม่ถูกจัด",
        value=True,
        help="หากเงื่อนไขแน่นเกินไป ระบบจะแสดงคาบที่ยังจัดไม่ได้แทนการสร้างตารางผิดเงื่อนไข",
    )
    st.sidebar.caption("แสดงคาบที่ติดเงื่อนไขแทนการบังคับให้ตารางผิด")
    time_limit = st.sidebar.slider(
        "เวลาคำนวณสูงสุด (วินาที)",
        10,
        180,
        45,
        5,
        help="เวลาสูงสุดที่ OR-Tools ใช้ค้นหาคำตอบในแต่ละครั้ง",
    )
    st.sidebar.caption("เพิ่มเวลาเพื่อเปิดโอกาสให้ระบบค้นหาตารางที่ดีกว่า")
    with st.sidebar.expander("น้ำหนักเป้าหมายขั้นสูง"):
        preference_weight = st.number_input(
            "Preference",
            min_value=0,
            max_value=200,
            value=75,
            help="ความสำคัญของคะแนนความต้องการเวลาสอนของอาจารย์",
        )
        room_fit_weight = st.number_input(
            "ความพอดีของห้อง",
            min_value=0,
            max_value=100,
            value=20,
            help="ความสำคัญของการเลือกห้องที่ความจุใกล้จำนวนผู้เรียน",
        )
        late_slot_penalty = st.number_input(
            "โทษคาบเย็น",
            min_value=0,
            max_value=100,
            value=8,
            help="ค่าปรับสำหรับคาบช่วงท้ายวัน ยิ่งสูงยิ่งหลีกเลี่ยงคาบเย็น",
        )
    return ScheduleParameters(
        max_teaching_hours_per_day=float(max_teach),
        max_study_hours_per_day=float(max_study),
        max_course_hours_per_day=float(max_course),
        max_courses_per_teacher_per_day=int(max_courses),
        strict_zero_preference=strict_zero,
        avoid_consecutive_days=avoid_consecutive,
        allow_unassigned=allow_unassigned,
        preference_weight=int(preference_weight),
        room_fit_weight=int(room_fit_weight),
        late_slot_penalty=int(late_slot_penalty),
        time_limit_seconds=int(time_limit),
    )


def main() -> None:
    inject_styles()
    parameters = sidebar_parameters()
    st.markdown(
        """
        <section class="hero">
          <div class="eyebrow">SchEDU · KKU TIMETABLE STUDIO</div>
          <h1>จัดตารางที่ตรวจสอบได้<br>พร้อมใช้กับทุกสาขา</h1>
          <p>อัปโหลดข้อมูลมาตรฐาน 5 ชีท กำหนดข้อจำกัด แล้วให้ระบบสร้างตารางโดยคุมอาจารย์ กลุ่มนักศึกษา ห้องเรียน และเวลาชนกันในคราวเดียว</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    upload_column, template_column = st.columns([1.8, 1])
    with upload_column:
        st.markdown('<div class="step"><b>1</b> เลือกข้อมูล</div>', unsafe_allow_html=True)
        main_file = st.file_uploader(
            "ไฟล์ข้อมูลหลัก (.xlsx)",
            type=["xlsx"],
            help="ไฟล์หลักประกอบด้วย all_courses, department_courses, student_courses, rooms และ student_groups",
        )
        st.caption(
            "ข้อมูลหลัก 5 ชีทสำหรับรายวิชา ผู้เรียน ห้อง และกลุ่มนักศึกษา "
            "รองรับรูปแบบเดิม teach/study/student/room เพื่อช่วยย้ายข้อมูล"
        )
    with template_column:
        st.markdown('<div class="step"><b>0</b> เริ่มจากตัวอย่าง</div>', unsafe_allow_html=True)
        sample_bytes = SAMPLE_FILE.read_bytes() if SAMPLE_FILE.exists() else b""
        if sample_bytes:
            st.download_button(
                "ดาวน์โหลดแม่แบบพร้อมข้อมูลตัวอย่าง",
                data=sample_bytes,
                file_name="SchEDU_input_template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
        use_sample = st.toggle(
            "ใช้ข้อมูลตัวอย่างในระบบ",
            value=main_file is None,
            disabled=not sample_bytes,
            help="เปิดเพื่อทดลองจัดตารางจากข้อมูลตัวอย่างโดยไม่ต้องอัปโหลดไฟล์",
        )

    input_data_guide()

    input_bytes = main_file.getvalue() if main_file else (sample_bytes if use_sample else None)
    if input_bytes is None:
        st.info("อัปโหลดไฟล์หลัก หรือเปิด “ใช้ข้อมูลตัวอย่างในระบบ” เพื่อเริ่ม")
        return

    with st.spinner("กำลังตรวจโครงสร้างข้อมูล…"):
        bundle = read_main(input_bytes)

    data_overview(bundle)
    if bundle.has_errors:
        st.stop()

    st.markdown(
        '<div class="step"><b>2</b> เพิ่ม Preference เวลาสอน</div>',
        unsafe_allow_html=True,
    )
    preference_file = st.file_uploader(
        "Preference เวลาสอนของอาจารย์ (ไม่บังคับ)",
        type=["xlsx"],
        key="preference_file",
        help="คะแนนความต้องการของอาจารย์ในแต่ละช่วงเวลา โดย 0 คือไม่พร้อมสอน และคะแนนสูงกว่าคือช่วงที่ต้องการมากกว่า",
    )
    st.caption(
        "ถ้าไม่อัปโหลด ระบบจะใช้คะแนนกลางเท่ากันทุกช่วงเวลา "
        "ไฟล์นี้ใช้จัดลำดับความเหมาะสมของเวลาและกำหนดช่วงที่อาจารย์ไม่พร้อม"
    )
    bundle = apply_preference_input(
        bundle,
        preference_file.getvalue() if preference_file else None,
    )
    st.caption(
        f"อ่าน Preference {len(bundle.preferences):,} อาจารย์ · "
        "ไฟล์ถูกประมวลผลใน session นี้เท่านั้น"
    )

    st.markdown('<div class="step"><b>3</b> สร้างตาราง</div>', unsafe_allow_html=True)
    if st.button("จัดตารางด้วย OR-Tools", type="primary", width="stretch"):
        with st.spinner("กำลังค้นหาตารางที่เหมาะสม…"):
            st.session_state.schedule_result = run_solver_isolated(bundle, parameters)
            st.session_state.schedule_parameters = parameters
            st.session_state.schedule_bundle = bundle

    if "schedule_result" in st.session_state:
        render_schedule(
            st.session_state.schedule_bundle,
            st.session_state.schedule_result,
            st.session_state.schedule_parameters,
        )

if __name__ == "__main__":
    main()
