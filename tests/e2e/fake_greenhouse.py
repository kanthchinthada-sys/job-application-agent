"""A small FAKE Greenhouse application form, built like the real one (ids, comboboxes, Attach buttons,
"Submit application"). Lets the filler be tested without ever touching a real employer's site.

Modes (query string ?mode=...):
  ok          submit -> "Thank you for applying" confirmation page
  uncertain   submit -> a page with no confirmation text
  error       submit -> HTTP 422 with a validation error (nothing submitted)
  badupload   file inputs never show the chosen file name (upload "not accepted")
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

FORM = """<!DOCTYPE html><html><body>
<h1>Senior QA Engineer (FICTIONAL) at Examplecorp</h1>
<form id="application-form" method="post" action="{action}" enctype="multipart/form-data">
  <label for="first_name">First Name*</label><input id="first_name" name="first_name" type="text">
  <label for="last_name">Last Name*</label><input id="last_name" name="last_name" type="text">
  <label for="email">Email*</label><input id="email" name="email" type="text">
  <label for="country">Country*</label><input id="country" name="country" role="combobox" data-options="Canada|United States">
  <label for="phone">Phone*</label><input id="phone" name="phone" type="tel">
  <label for="candidate-location">Location (City)*</label><input id="location" name="location" type="text">
  <div>Resume/CV* <input id="resume" name="resume" type="file"><span class="filename" data-for="resume"></span></div>
  <div>Cover Letter <input id="cover_letter" name="cover_letter" type="file"><span class="filename" data-for="cover_letter"></span></div>
  <label for="question_1">How did you hear about this job?*</label><input id="question_1" name="question_1" type="text">
  <label for="question_2">LinkedIn Profile</label><input id="question_2" name="question_2" type="text">
  <label for="question_3">Do you now or will you in the future require immigration sponsorship?*</label>
  <input id="question_3" name="question_3" role="combobox" data-options="Yes|No">
  <input type="checkbox" id="question_4[]_99" name="question_4[]" value="99"><label for="question_4[]_99">Acknowledge/Confirm</label>
  <label for="question_5">Why do you want to work here?</label><textarea id="question_5" name="question_5"></textarea>
  <label for="gender">Gender</label><input id="gender" name="gender" role="combobox" data-options="Male|Female|Decline To Self Identify">
  <button type="submit">Submit application</button>
</form>
<ul role="listbox" id="menu" style="display:none"></ul>
<script>
  const BAD_UPLOAD = {bad_upload};
  // Searchable comboboxes like Greenhouse's: typing shows matching options; clicking one selects it.
  const menu = document.getElementById("menu");
  document.querySelectorAll("[role=combobox]").forEach(box => {{
    box.addEventListener("input", () => {{
      const opts = box.dataset.options.split("|").filter(o => o.toLowerCase().includes(box.value.toLowerCase()));
      menu.innerHTML = opts.map(o => `<li role="option">${{o}}</li>`).join("");
      menu.style.display = "block";
      menu.querySelectorAll("li").forEach(li => li.onclick = () => {{ box.value = li.textContent; box.dataset.chosen = li.textContent; menu.style.display = "none"; }});
    }});
  }});
  document.querySelectorAll("input[type=file]").forEach(input => input.addEventListener("change", () => {{
    if (!BAD_UPLOAD) document.querySelector(`.filename[data-for="${{input.id}}"]`).textContent = input.files[0].name;
  }}));
</script></body></html>"""


def make_app() -> FastAPI:
    app = FastAPI()
    app.state.posts = []   # every form submission the fake employer received

    @app.get("/examplecorp/jobs/111", response_class=HTMLResponse)
    def form(mode: str = "ok"):
        return FORM.format(action=f"/examplecorp/jobs/111?mode={mode}", bad_upload="true" if mode == "badupload" else "false")

    @app.post("/examplecorp/jobs/111")
    async def submit(request: Request, mode: str = "ok"):
        data = await request.form()
        app.state.posts.append({k: (v.filename if hasattr(v, "filename") else v) for k, v in data.multi_items()})
        if mode == "error":
            return HTMLResponse("<p>First Name can't be blank</p>", status_code=422)
        if mode == "uncertain":
            return HTMLResponse("<p>Processing...</p>")
        return HTMLResponse("<h1>Thank you for applying.</h1><p>Your application has been submitted.</p>")

    return app
