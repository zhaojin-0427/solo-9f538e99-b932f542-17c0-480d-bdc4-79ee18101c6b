from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

_AMOUNT_RE = re.compile(r"^[0-9]+$")
MAX_AMOUNT = 10 ** 15


class CsvError(ValueError):
    pass


@dataclass
class ParsedCsv:
    row_labels: list
    col_labels: list
    amounts: list


def parse_csv(raw: bytes) -> ParsedCsv:
    """解析 CSV：首行首格为空/“院系”，其余为首行科目名；首列为院系名。"""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvError("CSV 需为 UTF-8 编码。") from exc
    if not text.strip():
        raise CsvError("CSV 内容为空。")
    if "\x00" in text:
        raise CsvError("CSV 含非法字符。")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if len(rows) < 2:
        raise CsvError("CSV 至少需要一行表头和一行院系数据。")
    header = rows[0]
    if len(header) < 2:
        raise CsvError("CSV 至少需要一列支出科目。")
    col_labels = [h.strip() for h in header[1:]]
    if any(not c for c in col_labels):
        raise CsvError("支出科目名称不能为空。")
    if len(set(col_labels)) != len(col_labels):
        raise CsvError("支出科目名称不能重复。")
    if len(col_labels) > 10:
        raise CsvError("支出科目最多 10 列。")

    row_labels = []
    amounts = []
    for lineno, row in enumerate(rows[1:], start=2):
        if len(row) > len(col_labels) + 1:
            raise CsvError(f"第 {lineno} 行多出数据列。")
        label = row[0].strip() if row else ""
        if not label:
            raise CsvError(f"第 {lineno} 行院系名称为空。")
        row_labels.append(label)
        vals = []
        for j in range(len(col_labels)):
            raw_cell = row[j + 1].strip() if j + 1 < len(row) else ""
            if raw_cell == "":
                raise CsvError(f"第 {lineno} 行第 {j + 2} 列金额为空。")
            if not _AMOUNT_RE.fullmatch(raw_cell):
                raise CsvError(f"第 {lineno} 行第 {j + 2} 列金额须为非负整数元：{raw_cell!r}")
            amount = int(raw_cell)
            if amount > MAX_AMOUNT:
                raise CsvError(f"第 {lineno} 行第 {j + 2} 列金额超出可处理范围。")
            vals.append(amount)
        amounts.append(vals)
    if not row_labels:
        raise CsvError("CSV 没有院系数据行。")
    if len(row_labels) > 20:
        raise CsvError("院系行最多 20 行。")
    if len(set(row_labels)) != len(row_labels):
        raise CsvError("院系名称不能重复。")
    return ParsedCsv(row_labels, col_labels, amounts)
