#!/usr/bin/env python3
"""Build architecture.pdf from architecture.md + diagram PNGs.

Uses pandoc for markdown→HTML, then Puppeteer (via mmdc's Chromium) for HTML→PDF.
"""
import base64
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).parent


def b64_img(name: str) -> str:
    data = (DOCS / name).read_bytes()
    encoded = base64.b64encode(data).decode()
    return f"data:image/png;base64,{encoded}"


def build_html_body() -> str:
    """Convert markdown to HTML body via pandoc."""
    md = (DOCS / "architecture.md").read_text()
    # Strip YAML frontmatter
    if md.startswith("---"):
        end = md.index("---", 3)
        md = md[end + 3:].lstrip()
    # Replace LaTeX figure blocks with HTML image tags
    import re
    # Remove all \begin{figure}...\end{figure} and \newpage
    md = re.sub(r'\\begin\{figure\}.*?\\end\{figure\}', '', md, flags=re.DOTALL)
    md = md.replace(r'\newpage', '')
    result = subprocess.run(
        ["pandoc", "--from=gfm", "--to=html5", "--no-highlight"],
        input=md, capture_output=True, text=True, check=True,
    )
    return result.stdout


def build_full_html(body: str) -> str:
    sizes_img = b64_img("architecture-sizes.png")
    subsystems_img = b64_img("architecture-subsystems.png")
    dataflow_img = b64_img("architecture-dataflow.png")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{
    size: A4;
    margin: 20mm 18mm;
    @top-center {{ content: "Overcode Architecture"; font-size: 9pt; color: #666; }}
    @bottom-center {{ content: counter(page); font-size: 9pt; color: #666; }}
  }}
  body {{
    font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.55;
    color: #1a1a1a;
    max-width: 100%;
  }}
  h1 {{
    font-size: 22pt;
    border-bottom: 2px solid #333;
    padding-bottom: 6px;
    margin-top: 36pt;
    page-break-after: avoid;
  }}
  h1:first-of-type {{
    font-size: 28pt;
    text-align: center;
    border-bottom: none;
    margin-top: 60pt;
    margin-bottom: 4pt;
  }}
  h2 {{
    font-size: 15pt;
    color: #2c5f8a;
    margin-top: 24pt;
    page-break-after: avoid;
  }}
  h3 {{
    font-size: 12pt;
    color: #444;
    margin-top: 18pt;
    page-break-after: avoid;
  }}
  p {{ margin: 8pt 0; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12pt 0;
    font-size: 10pt;
    page-break-inside: avoid;
  }}
  th {{
    background: #2c5f8a;
    color: white;
    padding: 6pt 10pt;
    text-align: left;
    font-weight: 600;
  }}
  td {{
    padding: 5pt 10pt;
    border-bottom: 1px solid #ddd;
  }}
  tr:nth-child(even) {{ background: #f5f7fa; }}
  code {{
    background: #f0f0f0;
    padding: 1pt 4pt;
    border-radius: 3px;
    font-size: 10pt;
    font-family: 'SF Mono', 'Menlo', monospace;
  }}
  pre {{
    background: #1e1e2e;
    color: #cdd6f4;
    padding: 12pt 16pt;
    border-radius: 6px;
    font-size: 9.5pt;
    line-height: 1.45;
    overflow-x: auto;
    page-break-inside: avoid;
  }}
  pre code {{
    background: none;
    padding: 0;
    color: inherit;
  }}
  strong {{ color: #1a1a1a; }}
  .diagram {{
    text-align: center;
    margin: 20pt 0;
    page-break-inside: avoid;
  }}
  .diagram img {{
    max-width: 100%;
    border: 1px solid #ddd;
    border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }}
  .diagram .caption {{
    font-size: 9.5pt;
    color: #666;
    font-style: italic;
    margin-top: 6pt;
  }}
  .title-page {{
    text-align: center;
    padding-top: 100pt;
  }}
  .subtitle {{
    font-size: 14pt;
    color: #666;
    margin-top: 4pt;
  }}
  .date {{
    font-size: 11pt;
    color: #999;
    margin-top: 20pt;
  }}
  .page-break {{ page-break-before: always; }}
  ol, ul {{ margin: 6pt 0; padding-left: 24pt; }}
  li {{ margin: 3pt 0; }}
  blockquote {{
    border-left: 3px solid #2c5f8a;
    margin: 12pt 0;
    padding: 8pt 16pt;
    background: #f5f7fa;
    color: #444;
  }}
</style>
</head>
<body>

<div class="title-page">
  <h1>Overcode Architecture</h1>
  <div class="subtitle">System Design &amp; Improvement Roadmap</div>
  <div class="date">February 2026</div>
  <div style="margin-top: 60pt;">
    <div class="diagram">
      <img src="{sizes_img}" style="max-width: 70%;">
      <div class="caption">Source lines by subsystem (28,950 total)</div>
    </div>
  </div>
</div>

<div class="page-break"></div>

<h1>System Architecture</h1>

<div class="diagram">
  <img src="{subsystems_img}">
  <div class="caption">Module dependency graph &mdash; arrows show import/data flow direction</div>
</div>

<div class="page-break"></div>

<h1>Data Flow</h1>

<p>The core data pipeline is simple: tmux panes are scraped for status, the monitor daemon aggregates everything into a single JSON file, and all UIs read that file.</p>

<div class="diagram">
  <img src="{dataflow_img}">
  <div class="caption">Runtime data flow</div>
</div>

{body}

</body>
</html>"""


def html_to_pdf(html_path: Path, pdf_path: Path):
    """Use Puppeteer (via mmdc's npx cache) to print HTML to PDF."""
    npx_dir = Path.home() / ".npm/_npx/668c188756b835f3/node_modules"
    js = f"""
const puppeteer = require('{npx_dir}/puppeteer');
(async () => {{
  const browser = await puppeteer.launch({{
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  }});
  const page = await browser.newPage();
  await page.goto('file://{html_path.resolve()}', {{waitUntil: 'networkidle0'}});
  await page.pdf({{
    path: '{pdf_path.resolve()}',
    format: 'A4',
    printBackground: true,
    margin: {{ top: '20mm', bottom: '20mm', left: '18mm', right: '18mm' }},
    displayHeaderFooter: false,
  }});
  await browser.close();
}})();
"""
    js_path = DOCS / "_build_pdf.js"
    js_path.write_text(js)
    try:
        subprocess.run(["node", str(js_path)], check=True, cwd=str(DOCS))
    finally:
        js_path.unlink(missing_ok=True)


def main():
    print("Building HTML body from markdown...")
    body = build_html_body()

    # Remove the first few sections from body that we already rendered as diagrams
    # (Overview, System Architecture, Data Flow + Pipeline subsections)
    # We keep everything from "# Subsystem Breakdown" onward
    import re
    # Find where "Subsystem Breakdown" starts in the HTML
    match = re.search(r'<h1[^>]*>Subsystem Breakdown</h1>', body)
    if match:
        body = body[match.start():]

    print("Building full HTML with embedded diagrams...")
    html = build_full_html(body)
    html_path = DOCS / "architecture.html"
    html_path.write_text(html)

    print("Converting HTML to PDF via Puppeteer...")
    pdf_path = DOCS / "architecture.pdf"
    html_to_pdf(html_path, pdf_path)

    print(f"Done: {pdf_path}")
    print(f"Size: {pdf_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
