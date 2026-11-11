"""Build the PDF and Word sample CVs. Run from the project folder:

    pip install -r requirements-dev.txt
    python scripts/build_samples.py

Both people are invented. Emails use the reserved example.com/.org domains and phone
numbers come from Ofcom's range set aside for drama (07700 900000 to 900999).
"""

from datetime import UTC, datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt
from fpdf import FPDF

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
NOTICE = "Fictional person, invented for testing Intake. Contact details use reserved example ranges."

AMARA = {
    "name": "Amara Osei",
    "headline": "Senior DevOps Engineer",
    "contact": ["Leeds, West Yorkshire | 07700 900123 | amara.osei@example.com",
                "linkedin.com/in/amara-osei-example | github.com/amara-osei-example"],
    "sections": [
        ("PROFILE", ["Platform engineer with ten years in IT and infrastructure, the last eight building CI/CD "
                     "pipelines and running Kubernetes in production. I like boring, well-tested infrastructure."]),
        ("EXPERIENCE", [
            "Senior DevOps Engineer | Copperleaf Systems | Mar 2021 - Present",
            "- Moved 40 services from hand-built VMs to Kubernetes (EKS), managed with Terraform",
            "- Cut median deploy time from 25 minutes to 6 with GitHub Actions caching",
            "- Ran the on-call rota and wrote the incident runbooks",
            "",
            "DevOps Engineer | Kestrel Freight Ltd | Jun 2018 - Feb 2021",
            "- Built the first CI pipelines for a 30-person engineering team",
            "- Introduced Prometheus and Grafana monitoring",
            "",
            "IT Support Analyst | Harbourline Retail | Sep 2016 - May 2018",
            "- Supported 300 staff across 12 stores",
            "- Automated new-starter accounts with PowerShell",
        ]),
        ("EDUCATION", ["BSc (Hons) Computer Science, 2:1", "University of Leeds, 2013 - 2016"]),
        ("SKILLS", [
            "Cloud: AWS, GCP",
            "Containers: Kubernetes, Docker, Helm",
            "Infrastructure as code: Terraform, Ansible",
            "CI/CD: GitHub Actions, Jenkins",
            "Languages: Python, Bash, Go",
            "Observability: Prometheus, Grafana",
        ]),
    ],
}


def build_pdf(path: Path) -> None:
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 10, AMARA["name"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 7, AMARA["headline"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9.5)
    for line in AMARA["contact"]:
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    for heading, lines in AMARA["sections"]:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, heading, new_x="LMARGIN", new_y="NEXT")
        for line in lines:
            if not line:
                pdf.ln(2)
                continue
            bold = " | " in line
            pdf.set_font("Helvetica", "B" if bold else "", 10)
            pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, NOTICE, new_x="LMARGIN", new_y="NEXT")
    pdf.set_creator("Intake sample builder")
    pdf.set_creation_date(datetime(2026, 11, 11, tzinfo=UTC))  # reproducible output
    pdf.output(str(path))


def build_docx(path: Path) -> None:
    doc = Document()
    doc.core_properties.author = "Intake sample builder"
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    # Name and title in the page header, the way many Word CV templates do it.
    header = doc.sections[0].header
    name = header.paragraphs[0]
    run = name.add_run("TOM BRENNAN")
    run.bold, run.font.size = True, Pt(18)
    header.add_paragraph("Warehouse Team Leader")

    for line in ["Address: Salford, Greater Manchester", "Mobile: +44 7700 900456", "Email: tom.brennan@example.org"]:
        doc.add_paragraph(line)

    doc.add_heading("Personal Statement", level=2)
    doc.add_paragraph("Reliable team leader with experience running a 20-person night shift in a busy "
                      "distribution centre. Licensed on counterbalance and reach trucks.")

    doc.add_heading("Employment History", level=2)
    roles = [
        ("Warehouse Team Leader", "Fernvale Distribution, Trafford Park", "January 2020 to Present",
         ["Lead a night shift of 20 pickers and loaders",
          "Kept pick accuracy above 99.5% for three years running",
          "Trained 15 new starters on safe manual handling"]),
        ("Warehouse Operative", "Fernvale Distribution, Trafford Park", "August 2017 to December 2019",
         ["Picked and packed up to 900 units a shift", "Promoted to team leader after two years"]),
        ("Retail Assistant (part-time)", "Brightmoor Supermarkets, Salford", "2015 to 2017",
         ["Stock rotation and customer service"]),
    ]
    for title, company, dates, bullets in roles:
        doc.add_paragraph().add_run(title).bold = True
        doc.add_paragraph(company)
        doc.add_paragraph(dates)
        for bullet in bullets:
            doc.add_paragraph(bullet, style="List Bullet")

    doc.add_heading("Key Skills", level=2)
    skills = ["Forklift licence (counterbalance and reach)", "Stock control", "WMS (Manhattan)",
              "Team leadership", "First aid at work", "Health and safety"]
    table = doc.add_table(rows=2, cols=3)
    for i, skill in enumerate(skills):
        table.cell(i // 3, i % 3).text = skill

    doc.add_heading("Education and Training", level=2)
    for line in ["Level 3 Diploma in Warehousing and Storage, 2021", "Salford City College",
                 "GCSEs (9 including English and Maths), 2015", "Irwell Park High School"]:
        doc.add_paragraph(line)
    doc.add_paragraph("References available on request.")
    doc.add_paragraph().add_run(NOTICE).italic = True
    doc.save(str(path))


if __name__ == "__main__":
    build_pdf(SAMPLES / "amara-osei.pdf")
    build_docx(SAMPLES / "tom-brennan.docx")
    print("wrote", SAMPLES / "amara-osei.pdf", "and", SAMPLES / "tom-brennan.docx")
