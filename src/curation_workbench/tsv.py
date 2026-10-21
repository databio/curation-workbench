"""Round-trip TSV tables for the curated mapping files and the term-id ledger.

SSSOM files, the term-id ledger and any other curated TSV share one shape:
leading `#` comment lines (the SSSOM metadata block, or a header note), one
header row, then tab-separated data rows. `Table` keeps the comment block
and column order, so an edit diffs only the rows it touched. Columns are read
and written by header name, never by position.
"""
import csv
import io


class Table:
    def __init__(self, comments, header, rows):
        self.comments = comments   # the leading '#' lines, verbatim (with newlines)
        self.header = header       # column names, in order
        self.rows = rows           # list of dicts keyed by column name

    @classmethod
    def parse(cls, text):
        lines = text.splitlines(keepends=True)
        i = 0
        while i < len(lines) and lines[i].startswith("#"):
            i += 1
        comments = lines[:i]
        body = [ln for ln in lines[i:] if not ln.startswith("#")]
        reader = csv.DictReader(body, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None)
        rows = [{k: (v or "") for k, v in r.items()} for r in reader]
        return cls(comments, list(reader.fieldnames or []), rows)

    def add_column(self, name, after=None):
        """Add column `name` (after `after`, else at the end) if absent."""
        if name in self.header:
            return
        pos = self.header.index(after) + 1 if after in self.header else len(self.header)
        self.header.insert(pos, name)
        for r in self.rows:
            r.setdefault(name, "")

    def dumps(self):
        buf = io.StringIO()
        buf.write("".join(self.comments))
        w = csv.DictWriter(buf, self.header, delimiter="\t", lineterminator="\n",
                           quoting=csv.QUOTE_NONE, quotechar=None, extrasaction="raise")
        w.writeheader()
        for r in self.rows:
            w.writerow({k: r.get(k, "") or "" for k in self.header})
        return buf.getvalue()
