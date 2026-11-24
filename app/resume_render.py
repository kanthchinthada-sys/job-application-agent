"""Render a tailored resume (JSON) as a printable HTML page in the user's own resume style.

Layout follows the user's resume: serif font, large bold name, contact line joined by " | ", a heavy rule,
bold upper-case section headings, "Company | Location" with right-aligned dates, bold title, bullet list,
"Recognition:" under the role it belongs to, headed skill groups, plain certification lines.

Open it in the browser and use File > Print > Save as PDF. Text is editable on the page for last-minute
tweaks before printing; those tweaks are not saved back to the app.
"""

import re
from datetime import datetime
from html import escape

from app.tailoring import TailoredResume


def _month(ym: str | None) -> str:
    if not ym:
        return "Present"
    return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")


def _short_link(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url)).rstrip("/")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def render(resume: TailoredResume, job_title: str, company: str) -> str:
    c = resume.contact
    contact = " | ".join(escape(x) for x in [c.get("email"), c.get("phone"), *[_short_link(u) for u in c.get("links", [])]] if x)

    if resume.skill_groups:
        skills = "".join(f"<p class='skill'><b>{escape(g['name'])}:</b> {escape(', '.join(g['skills']))}</p>"
                         for g in resume.skill_groups)
    else:
        skills = f"<p class='skill'>{escape(', '.join(resume.skills))}</p>"

    def role_html(r) -> str:
        where = escape(r.company) + (f" | {escape(r.location)}" if r.location else "")
        recognition = f"<p class='recog'><b>Recognition:</b> {escape(r.recognition)}</p>" if r.recognition else ""
        return f"""<div class="role">
          <div class="line"><b>{where}</b><b>{_month(r.start_date)} - {_month(r.end_date)}</b></div>
          <div><b>{escape(r.title)}</b></div>
          <ul>{"".join(f"<li>{escape(b.text)}</li>" for b in r.bullets)}</ul>{recognition}
        </div>"""

    roles = "".join(role_html(r) for r in resume.experience)

    projects = "".join(
        f"""<div class="role">
          <div class="line"><span><b>{escape(p['name'])}</b>{' | ' + escape(_short_link(p['link'])) if p.get('link') else ''}</span></div>
          <ul>{"".join(f"<li>{escape(s)}</li>" for s in (p.get('highlights') or _sentences(p['description'])))}
          {f"<li><b>Tech:</b> {escape(', '.join(p['skills']))}</li>" if p.get('skills') else ''}</ul>
        </div>"""
        for p in resume.projects
    )
    education = "".join(
        f"<p class='plain'><b>{escape(e['degree'])}</b> | {escape(e['institution'])}<br>"
        f"{escape(e.get('location') or '')}{' | ' if e.get('location') and e.get('start_date') else ''}"
        f"{(_month(e.get('start_date')) + ' - ' + _month(e.get('end_date'))) if e.get('start_date') else ''}</p>"
        for e in resume.education
    )
    certs = "".join(f"<div>{escape(x)}</div>" for x in resume.certifications)
    achievements = "".join(f"<div>{escape(x)}</div>" for x in resume.achievements)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{escape(resume.name)} - Resume - {escape(company)}</title>
<style>
{_page_css("0.5in", "0.6in")}
  body {{ font: 10.5pt/1.25 "Times New Roman", Times, serif; color:#000; max-width: 7.3in; margin: 24px auto; }}
  h1 {{ font-size: 25pt; margin: 0 0 2px; font-weight: bold; }}
  .contact {{ font-size: 10pt; margin-bottom: 6px; }}
  hr.rule {{ border: 0; border-top: 2.5px solid #000; margin: 4px 0 12px; }}
  h2 {{ font-size: 11pt; font-weight: bold; text-transform: uppercase; margin: 12px 0 5px; }}
  p {{ margin: 0; text-align: justify; }}
  p.skill {{ margin: 0 0 3px; }}
  p.plain {{ text-align: left; }}
  .role {{ margin-bottom: 7px; }}
  .line {{ display: flex; justify-content: space-between; gap: 12px; }}
  ul {{ margin: 2px 0 0 0; padding-left: 18px; }}
  li {{ text-align: justify; margin: 0 0 1px; }}
  .recog {{ margin-top: 2px; }}
  h2, .line, .role > div:not(.line) {{ break-after: avoid; page-break-after: avoid; }}  /* headings stay with their text */
  li, p.skill {{ break-inside: avoid; page-break-inside: avoid; }}
  .toolbar {{ font: 13px -apple-system, sans-serif; background:#eef2ff; color:#2f5bea; padding: 8px 12px;
              border-radius: 8px; margin-bottom: 16px; }}
  @media print {{ .toolbar {{ display:none; }} body {{ margin: 0; }} }}
  .embedded .toolbar {{ display: none; }}
</style>
<script>if (window.self !== window.top) {{   // shown inside the app as a preview: read-only, no toolbar
  document.documentElement.classList.add('embedded');
  addEventListener('DOMContentLoaded', () =>
    document.querySelectorAll('[contenteditable]').forEach(e => e.setAttribute('contenteditable', 'false')));
}}</script></head>
<body>
<div class="toolbar"><a href="/ui/" onclick="if (history.length > 1) {{ history.back(); return false; }}">← Back to the app</a>
  · Tailored for <b>{escape(job_title)}</b> at <b>{escape(company)}</b>.
  Click any text to tweak it, then <button type="button" onclick="window.print()">Print / Save as PDF</button>
  (tweaks here are not saved).</div>
{_sheet(f'''<div class="smart-justify" data-print-width="7.3in" contenteditable="true" spellcheck="true">
<h1>{escape(resume.name)}</h1>
<div class="contact">{contact}</div>
<hr class="rule">
<h2>Professional Summary</h2><p>{escape(resume.summary)}</p>
<h2>Core Skills</h2>{skills}
<h2>Professional Experience</h2>{roles}
{f'<h2>Projects</h2>{projects}' if projects else ''}
{f'<h2>Education</h2>{education}' if education else ''}
{f'<h2>Certifications and Professional Development</h2>{certs}' if certs else ''}
{f'<h2>Achievements</h2>{achievements}' if achievements else ''}
</div>''')}
</body></html>"""


SMART_JUSTIFY = r"""(function () {
  // Smart justification: justified text keeps its look, but a paragraph whose lines would show big gaps
  // between words gets a tiny, invisible spacing tweak that re-breaks its lines more evenly.
  // No hyphenation: hyphens would split keywords ("Kuber-netes") for ATS text readers.
  function lines(el) {
    var prev = el.style.textAlign; el.style.textAlign = 'left';
    var box = el.getBoundingClientRect(), right = box.right - parseFloat(getComputedStyle(el).paddingRight);
    var map = {}, keys = [], w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) {
      var re = /\S+/g, m;
      while ((m = re.exec(n.data))) {
        var r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
        var rs = r.getClientRects();
        for (var i = 0; i < rs.length; i++) {
          var k = Math.round(rs[i].top);
          if (!map[k]) { map[k] = {right: rs[i].right, words: 0}; keys.push(k); }
          map[k].right = Math.max(map[k].right, rs[i].right); map[k].words++;
        }
      }
    }
    el.style.textAlign = prev;
    keys.sort(function (a, b) { return a - b; });
    var worst = 0;
    for (var j = 0; j < keys.length - 1; j++) {
      var L = map[keys[j]];
      if (L.words > 1) worst = Math.max(worst, (right - L.right) / (L.words - 1));
    }
    return {count: keys.length, worst: worst};
  }
  function tune(el) {
    el.style.letterSpacing = ''; el.style.wordSpacing = ''; el.style.textAlign = '';
    var base = lines(el);
    if (base.count < 2 || base.worst <= 3) return;
    var best = {ls: 0, ws: 0, worst: base.worst};
    // never narrower spaces between words: in the saved PDF, text readers (and ATS parsers) would see
    // "Jenkinsfailures" for "Jenkins failures"; only a hair of letter spacing, and wider word spacing
    var LS = [-0.1, -0.2, -0.3, 0.1, 0.2], WS = [0];
    for (var a = 0; a < LS.length; a++) for (var b = 0; b < WS.length; b++) {
      el.style.letterSpacing = LS[a] + 'px'; el.style.wordSpacing = WS[b] + 'px';
      var t = lines(el);
      if (t.count <= base.count && t.worst < best.worst - 0.3) best = {ls: LS[a], ws: WS[b], worst: t.worst};
    }
    el.style.letterSpacing = best.ls ? best.ls + 'px' : '';
    el.style.wordSpacing = best.ws ? best.ws + 'px' : '';
    if (best.worst > 5) el.style.textAlign = 'left';   // still gappy: a ragged edge looks better than holes
  }
  function run() {
    // measure at the PRINTED width, whatever the size of the browser window
    var hosts = document.querySelectorAll('.smart-justify');
    for (var h = 0; h < hosts.length; h++) hosts[h].style.width = hosts[h].getAttribute('data-print-width') || '';
    var els = document.querySelectorAll('.smart-justify p, .smart-justify li');
    for (var i = 0; i < els.length; i++) if (getComputedStyle(els[i]).textAlign === 'justify' || els[i].style.textAlign) tune(els[i]);
    for (h = 0; h < hosts.length; h++) if (!matchMedia('print').matches) hosts[h].style.width = '';
  }
  window.smartJustify = run;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
  window.addEventListener('beforeprint', run);
})();
"""


def _page_css(top_bottom: str, sides: str) -> str:
    """Print with no page margin, so the browser has nowhere to print its header and footer (date, title,
    URL, page numbers). The margins are recreated inside the page: side padding on the body, and a spacer
    header/footer row of a table, which the browser repeats on every printed page."""
    return f"""
  @page {{ size: Letter; margin: 0; }}
  table.sheet {{ border-collapse: collapse; width: 100%; }}
  table.sheet td {{ padding: 0; }}
  .space {{ display: none; }}
  @media print {{
    body {{ margin: 0 !important; padding: 0 {sides}; }}   /* keeps its max-width: text width = page - margins */
    .space {{ display: block; height: {top_bottom}; }}
  }}"""


def _sheet(content: str) -> str:
    return ("<table class='sheet'><thead><tr><td><div class='space'></div></td></tr></thead>"
            "<tfoot><tr><td><div class='space'></div></td></tr></tfoot>"
            f"<tbody><tr><td>{content}</td></tr></tbody></table>"
            f"<script>{SMART_JUSTIFY}</script>")


def render_letter(text: str, name: str, contact: dict, job_title: str, company: str) -> str:
    """A cover letter page in the same style as the resume (for printing / uploading as PDF)."""
    contact_line = " | ".join(escape(x) for x in [contact.get("email"), contact.get("phone"),
                                                  *[_short_link(u) for u in contact.get("links", [])]] if x)
    paragraphs = "".join(f"<p>{escape(p)}</p>" for p in text.split("\n\n"))
    paragraphs = paragraphs.replace("\n", "<br>")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{escape(name)} - Cover letter - {escape(company)}</title>
<style>
{_page_css("0.8in", "0.8in")}
  body {{ font: 11pt/1.4 "Times New Roman", Times, serif; color:#000; max-width: 6.9in; margin: 24px auto; }}
  h1 {{ font-size: 22pt; margin: 0 0 2px; }} .contact {{ font-size: 10pt; }}
  hr.rule {{ border: 0; border-top: 2.5px solid #000; margin: 4px 0 18px; }}
  p {{ margin: 0 0 10px; text-align: justify; }}
</style></head><body>
{_sheet(f'''<div class="smart-justify" data-print-width="6.9in"><h1>{escape(name)}</h1><div class="contact">{contact_line}</div><hr class="rule">
<p>Re: {escape(job_title)}, {escape(company)}</p>
{paragraphs}</div>''')}
</body></html>"""
