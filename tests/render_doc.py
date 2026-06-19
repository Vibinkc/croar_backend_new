"""Render README.md -> README.html as a print-ready, PDF-friendly page.

Open the generated tests/README.html in a browser and use Print -> Save as PDF.
All tables are forced to fit the page width (no horizontal scroll): table-layout is fixed,
cells wrap long text, and code blocks soft-wrap.

Run: python tests/render_doc.py
"""

from pathlib import Path

import markdown

HERE = Path(__file__).parent
SRC = HERE / "README.md"
OUT = HERE / "README.html"

CSS = """
@page { size: A4 portrait; margin: 14mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 11.5px; line-height: 1.5; color: #1b1f23;
  max-width: 920px; margin: 0 auto; padding: 24px;
}
h1 { font-size: 22px; border-bottom: 2px solid #ddd; padding-bottom: 8px; }
h2 { font-size: 17px; margin-top: 28px; border-bottom: 1px solid #eee; padding-bottom: 5px; }
h3 { font-size: 14px; margin-top: 20px; }
blockquote {
  margin: 12px 0; padding: 8px 14px; background: #f3f7ff;
  border-left: 4px solid #6e8efb; color: #344;
}
a { color: #0b5cad; text-decoration: none; }

/* The key fix: tables always fit the page, cells wrap, nothing scrolls. */
table {
  width: 100%; table-layout: fixed; border-collapse: collapse;
  margin: 14px 0; font-size: 10.5px;
}
th, td {
  border: 1px solid #d0d7de; padding: 6px 8px; text-align: left;
  vertical-align: top;
  white-space: normal;
  overflow-wrap: break-word; word-break: break-word; hyphens: auto;
}
th { background: #f1f3f5; font-weight: 600; }
/* Narrow numeric "Tests" column when present (2nd col), wide text elsewhere. */
tr > td:nth-child(2), tr > th:nth-child(2) { width: 8%; text-align: right; }

/* Code blocks soft-wrap instead of overflowing. */
pre {
  background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px;
  padding: 12px; overflow: visible;
  white-space: pre-wrap; word-break: break-word; font-size: 10.5px;
}
code {
  background: #eef1f4; padding: 1px 4px; border-radius: 4px;
  font-family: "Cascadia Code", Consolas, monospace; font-size: 10.5px;
  overflow-wrap: break-word; word-break: break-word;
}
pre code { background: none; padding: 0; }
ul, ol { padding-left: 22px; }
hr { border: none; border-top: 1px solid #e1e4e8; margin: 22px 0; }

/* Avoid awkward page breaks when printing to PDF. */
@media print {
  body { max-width: none; padding: 0; }
  h2, h3 { page-break-after: avoid; }
  table, pre, blockquote { page-break-inside: avoid; }
  tr { page-break-inside: avoid; }
}
"""


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    body = markdown.markdown(
        text, extensions=["tables", "fenced_code", "toc", "sane_lists"], output_format="html5"
    )
    html = (
        "<!doctype html>\n<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<title>Croar Backend — Test Suite Documentation</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
