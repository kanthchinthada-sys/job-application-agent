import { Component, inject, input, OnInit, signal } from '@angular/core';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';
import { ResumeImport } from '../models';
import { Msg, MsgBox } from '../ui';
import { BankPanel } from './bank-panel';

/**
 * Your profile. New users start here: upload a resume, check what was read, save. Everything comes from your own
 * resume: the agent never invents qualifications. The JSON editor stays available for fine-tuning.
 */
@Component({
  selector: 'app-profile-page',
  imports: [FormsModule, MsgBox, BankPanel],
  template: `
    <div class="card" data-testid="resume-upload">
      @if (setup() && !hasProfile()) {
        <h2>Welcome! Set up your profile</h2>
        <p class="hint">One step: give the agent your experience. Upload your resume, or paste your experience as text.
          It is read on this computer and you check the result before it is saved. Only what you provide is ever used.</p>
      } @else if (hasProfile()) {
        <h2>Your profile</h2>
        <p class="hint" data-testid="profile-summary">{{ summary() }}</p>
        <p class="hint">Upload an updated resume to replace your profile (your experience bank, preferences and resume
          settings are kept).</p>
      } @else {
        <h2>Get started: upload your resume</h2>
        <p class="hint">PDF, Word (.docx) or text. It is read on this computer; nothing is sent anywhere. You check the
          result before it is saved. Only experience in your resume is used: the agent never invents qualifications.</p>
      }
      <div class="row">
        <input type="file" aria-label="Resume file" accept=".pdf,.docx,.txt,.md,.rtf,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
               (change)="pickFile($event)">
      </div>
      <details [open]="setup()">
        <summary class="small">…or paste your resume or experience as text</summary>
        <textarea aria-label="Resume text" [(ngModel)]="resumeText" placeholder="Paste your resume here"></textarea>
        <button class="secondary" type="button" [disabled]="busy()" (click)="readText()">Read resume</button>
      </details>
      <app-msg [msg]="uploadMsg()" />

      @if (preview(); as p) {
        @let d = draft()!;
        <div class="panel small" data-testid="resume-preview">
          <b>Check what was read, then save.</b> {{ (d.experience || []).length }} roles · {{ bulletCount(d) }} bullets ·
          {{ (d.projects || []).length }} projects · {{ (d.education || []).length }} education.
          @if (unsure().size) {
            <div class="msg info" data-testid="review-unsure">{{ unsure().size }} field{{ unsure().size > 1 ? 's' : '' }}
              couldn't be read confidently: they are marked <span class="unsure-chip">Check this</span>. Fix them here.</div>
          }
          @for (w of p.warnings; track $index) { <div class="warn-text">{{ w }}</div> }
          <div class="row">
            <div><label for="rv-name">Name @if (flag('full_name')) { <span class="unsure-chip">Check this</span> }</label>
              <input id="rv-name" [class.unsure]="flag('full_name')" [(ngModel)]="d.full_name"></div>
            <div><label for="rv-email">Email @if (flag('contact.email')) { <span class="unsure-chip">Not found</span> }</label>
              <input id="rv-email" [class.unsure]="flag('contact.email')" [(ngModel)]="d.contact.email"></div>
            <div><label for="rv-phone">Phone</label><input id="rv-phone" [(ngModel)]="d.contact.phone"></div>
          </div>
          <label for="rv-skills">Skills <span class="muted">(comma separated)</span>
            @if (flag('skills')) { <span class="unsure-chip">None found</span> }</label>
          <input id="rv-skills" [class.unsure]="flag('skills')" [(ngModel)]="skillsText">
          <h3>Experience</h3>
          @if (!(d.experience || []).length) {
            <div class="msg bad" data-testid="review-no-roles">No work experience was recognised. Add your roles below, or
              paste your resume again with each role on a line like
              <code>Senior QA Engineer, Example Corp, Toronto, ON | Jan 2021 - Present</code> followed by its bullets.</div>
          }
          @for (e of d.experience || []; track $index; let i = $index) {
            <div class="panel" [attr.data-testid]="'review-role-' + i">
              <div class="row">
                <div><label [for]="'rv-t' + i">Title @if (flag('experience.' + i + '.title')) { <span class="unsure-chip">Check this</span> }</label>
                  <input [id]="'rv-t' + i" [class.unsure]="flag('experience.' + i + '.title')" [(ngModel)]="e.title"></div>
                <div><label [for]="'rv-c' + i">Employer @if (flag('experience.' + i + '.company')) { <span class="unsure-chip">Check this</span> }</label>
                  <input [id]="'rv-c' + i" [class.unsure]="flag('experience.' + i + '.company')" [(ngModel)]="e.company"></div>
                <div><label [for]="'rv-l' + i">Location</label><input [id]="'rv-l' + i" [(ngModel)]="e.location"></div>
              </div>
              <div class="row">
                <div><label [for]="'rv-s' + i">Start (YYYY-MM)</label><input [id]="'rv-s' + i" [(ngModel)]="e.start_date" placeholder="2021-01"></div>
                <div><label [for]="'rv-e' + i">End (YYYY-MM, empty = present)</label><input [id]="'rv-e' + i" [(ngModel)]="e.end_date" placeholder="present"></div>
              </div>
              <label [for]="'rv-b' + i">Bullets <span class="muted">(one per line)</span>
                @if (flag('experience.' + i + '.highlights')) { <span class="unsure-chip">None found</span> }</label>
              <textarea [id]="'rv-b' + i" class="short" [class.unsure]="flag('experience.' + i + '.highlights')"
                        [ngModel]="(e.highlights || []).join('\n')" (ngModelChange)="e.highlights = splitLines($event)"></textarea>
              <button class="link-btn small" type="button" (click)="removeRole(i)">Remove this role</button>
            </div>
          }
          <button class="secondary" type="button" (click)="addRole()">Add a role</button>
          <div style="margin-top:8px">
            <button class="primary" type="button" (click)="savePreview(p)">{{ hasProfile() ? 'Replace my profile' : 'Save as my profile' }}</button>
            <button class="secondary" type="button" (click)="preview.set(null)">Cancel</button>
            @if (hasProfile()) { <span class="small muted">Your experience bank, preferences, resume settings and documents are kept.</span> }
          </div>
        </div>
      }
    </div>

    @if (localData()) {
      <div class="card" data-testid="local-data">
        <h2>Bring in existing local data <span class="muted small">(installation owner only)</span></h2>
        <p class="hint">This computer has data from before accounts were added. If it is yours, enter the setup code
          shown in the console where the app was started (also in <code>data/owner-setup-code.txt</code>). It is copied
          into your workspace once; the original file is kept as a backup. Your workspace must be empty.</p>
        <div class="row">
          <input class="wide" aria-label="Setup code" [(ngModel)]="setupCode" placeholder="XXXXXX-XXXXXX-XXXXXX">
          <button class="secondary tight" type="button" [disabled]="claiming() || !setupCode.trim()" (click)="claim()">Bring it in</button>
        </div>
        <app-msg [msg]="claimMsg()" />
      </div>
    }
    @if (setup() && hasProfile()) {
      <div class="card" data-testid="setup-done">
        <h2>You're set up</h2>
        <p>Next: add a job you want to apply for. You can add an experience bank and preferences on the Profile page later.</p>
        <button class="primary" type="button" (click)="router.navigate(['/add'])">Add a job</button>
        <button class="secondary" type="button" (click)="router.navigate(['/find'])">Find jobs</button>
      </div>
    }
    @if (!setup()) {
    <app-bank-panel />

    <div class="card">
      <h2>Edit as JSON (advanced)</h2>
      <p class="hint">Every detail of your profile, including <code>preferences</code> (target roles, locations) and
        <code>resume_settings</code> (bullets per role, focus themes).
        <button class="secondary tight" type="button" (click)="loadExample()">Load fictional example</button></p>
      <textarea class="code" id="profile-json" aria-label="Profile JSON" spellcheck="false" [(ngModel)]="json"></textarea>
      <button class="secondary" type="button" (click)="save()">Save profile</button>
      <app-msg [msg]="msg()" />
    </div>
    }
  `,
})
export class ProfilePage implements OnInit {
  private api = inject(Api);
  protected router = inject(Router);
  /** /welcome: the short first-run setup (route data). */
  readonly setup = input(false);

  protected readonly json = signal('');
  protected readonly msg = signal<Msg | null>(null);
  protected readonly uploadMsg = signal<Msg | null>(null);
  protected readonly hasProfile = signal(false);
  protected readonly summary = signal('');
  protected readonly resumeText = signal('');
  protected readonly busy = signal(false);
  protected readonly preview = signal<ResumeImport | null>(null);

  async ngOnInit() {
    this.api.me().then(m => this.localData.set(!!m.local_data_available)).catch(() => {});
    await this.load();
  }

  protected readonly localData = signal(false);
  protected readonly claiming = signal(false);
  protected readonly claimMsg = signal<Msg | null>(null);
  protected setupCode = '';

  async claim() {
    this.claiming.set(true);
    try {
      const r: any = await this.api.claimLocalData(this.setupCode.trim());
      this.claimMsg.set({ kind: 'ok', text: `Brought in ${r.jobs} jobs, ${r.applications} applications and `
        + `${r.documents} documents${r.has_profile ? ' and your profile' : ''}. Reloading…` });
      setTimeout(() => window.location.reload(), 900);
    } catch (e) {
      this.claimMsg.set({ kind: 'bad', text: (e as ApiError).message });
    } finally {
      this.claiming.set(false);
    }
  }

  private async load() {
    try {
      const p = await this.api.profile();
      delete p['updated_at'];
      delete p['experience_bank'];          // edited in the Experience bank section
      this.json.set(JSON.stringify(p, null, 2));
      this.hasProfile.set(true);
      const exp = (p['experience'] as unknown[] | undefined) ?? [];
      const skills = (p['skills'] as unknown[] | undefined) ?? [];
      this.summary.set(`${p['full_name']} · ${exp.length} roles · ${skills.length} skills.`);
    } catch (e) {
      const err = e as ApiError;
      this.hasProfile.set(false);
      if (err.status !== 404) this.msg.set({ kind: 'bad', text: err.message });
    }
  }

  protected bulletCount(profile: any): number {
    return (profile.experience || []).reduce((n: number, e: any) => n + (e.highlights || []).length, 0);
  }

  /** The editable copy of what was read (the review screen), and the fields the reader was unsure about. */
  protected readonly draft = signal<any>(null);
  protected readonly unsure = signal<Set<string>>(new Set());
  protected skillsText = '';
  protected flag(path: string) { return this.unsure().has(path); }
  protected splitLines(t: string) { return t.split('\n').map(x => x.trim()).filter(Boolean); }
  protected addRole() {
    this.draft().experience = [...(this.draft().experience || []),
      { title: '', company: '', location: '', start_date: '', end_date: '', highlights: [] }];
    this.draft.set({ ...this.draft() });
  }
  protected removeRole(i: number) {
    this.draft().experience = this.draft().experience.filter((_: unknown, j: number) => j !== i);
    this.draft.set({ ...this.draft() });
  }

  /** What the review screen holds, cleaned for saving. */
  private reviewed(): Record<string, unknown> {
    const d = structuredClone(this.draft());
    d.contact = Object.fromEntries(Object.entries(d.contact || {}).filter(([, v]) => v !== '' && v != null));
    d.skills = this.skillsText.split(',').map((x: string) => x.trim()).filter(Boolean);
    d.experience = (d.experience || []).filter((e: any) => e.company?.trim() || e.title?.trim()).map((e: any) => {
      const out: any = { ...e, title: e.title?.trim() || 'Role', company: e.company?.trim() || 'Unknown company' };
      if (!out.location) delete out.location;
      if (!out.end_date) delete out.end_date;
      return out;
    });
    return d;
  }

  async pickFile(event: Event) {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    if (file.size > 10_000_000) {
      this.uploadMsg.set({ kind: 'bad', text: 'That file is larger than 10 MB.' });
      return;
    }
    const base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    await this.read({ filename: file.name, content_base64: base64 });
  }

  async readText() {
    await this.read({ text: this.resumeText() });
  }

  private async read(body: { filename?: string; content_base64?: string; text?: string }) {
    this.busy.set(true);
    this.preview.set(null);
    this.uploadMsg.set({ kind: 'info', text: 'Reading your resume…' });
    try {
      const r = await this.api.readResume(body);
      const d = structuredClone(r.profile);
      d.contact = d.contact || {};
      this.draft.set(d);
      this.skillsText = (d.skills || []).join(', ');
      this.unsure.set(new Set(r.uncertain ?? []));
      this.preview.set(r);
      this.uploadMsg.set({ kind: 'ok', text: 'Here is what was read. Check it, then save.' });
    } catch (e) {
      this.uploadMsg.set({ kind: 'bad', text: (e as ApiError).message });
    } finally {
      this.busy.set(false);
    }
  }

  async savePreview(p: ResumeImport) {
    try {
      const current = await this.api.profile().catch(() => null) as Record<string, unknown> | null;
      const data: Record<string, unknown> = this.reviewed();
      // keep what the resume doesn't contain
      for (const key of ['experience_bank', 'preferences', 'resume_settings']) {
        if (current && current[key] !== undefined) data[key] = current[key];
      }
      if (current && !data['skill_groups'] && current['skill_groups']) data['skill_groups'] = current['skill_groups'];
      await this.api.saveProfile(data);
      this.preview.set(null);
      this.uploadMsg.set({ kind: 'ok', text: 'Profile saved. Next: add your experience bank (optional), then add a job or find jobs.' });
      await this.load();
    } catch (e) {
      this.uploadMsg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  async loadExample() {
    try {
      this.json.set(JSON.stringify(await this.api.exampleProfile(), null, 2));
      this.msg.set({ kind: 'info', text: 'Fictional example loaded. Not saved yet.' });
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  async save() {
    let data: unknown;
    try {
      data = JSON.parse(this.json());
    } catch {
      this.msg.set({ kind: 'bad', text: 'This box needs the profile in JSON format, not plain resume text. To use your resume, upload it at the top of this page.' });
      return;
    }
    try {
      // keep the experience bank (managed above) when saving the rest of the profile
      const current = await this.api.profile().catch(() => null) as { experience_bank?: unknown[] } | null;
      if (current?.experience_bank && data && typeof data === 'object' && !('experience_bank' in data)) {
        (data as Record<string, unknown>)['experience_bank'] = current.experience_bank;
      }
      await this.api.saveProfile(data);
      this.msg.set({ kind: 'ok', text: 'Profile saved.' });
      await this.load();
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }
}
