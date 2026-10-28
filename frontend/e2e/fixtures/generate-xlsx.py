"""Regenerate e2e-synthetic-questionnaire.xlsx (openpyxl available in backend dev extras)."""
import sys
from pathlib import Path

from openpyxl import Workbook

out = Path(__file__).parent / "e2e-synthetic-questionnaire.xlsx"
wb = Workbook()
ws = wb.active
ws.title = "Questionnaire"
ws.append(["Section", "Question"])
ws.append(["Security > Operational Controls", "How often does the duty officer review the alpha gate?"])
ws.append(["Security > Incidents", "Where is the synthetic incident register stored?"])
wb.save(out)
print(out, "written")
