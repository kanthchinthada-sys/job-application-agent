import { Component, inject, OnInit, signal } from '@angular/core';
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
      @if (hasProfile()) {
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
      <details>
        <summary class="small">…or paste your resume as text</summary>
        <textarea aria-label="Resume text" [(ngModel)]="resumeText" placeholder="Paste your resume here"></textarea>
        <button class="secondary" type="button" [disabled]="busy()" (click)="readText()">Read resume</button>
      </details>
      <app-msg [msg]="uploadMsg()" />

      @if (preview(); as p) {
        <div class="panel small" data-testid="resume-preview">
          <b>{{ p.profile.full_name }}</b>
          @if (p.profile.contact?.email) { · {{ p.profile.contact.email }} }
          <div>{{ (p.profile.experience || []).length }} roles ·
            {{ bulletCount(p) }} bullets · {{ (p.profile.skills || []).length }} skills ·
            {{ (p.profile.projects || []).length }} projects · {{ (p.profile.education || []).length }} education</div>
          @for (e of p.profile.experience || []; track $index) {
            <div>• <b>{{ e.title }}</b>, {{ e.company }} ({{ e.start_date }} – {{ e.end_date || 'present' }}):
              {{ (e.highlights || []).length }} bullets</div>
          }
          @if (p.warnings.length) {
            <div class="warn-text">Please check: {{ p.warnings.join(' ') }}</div>
          }
          <div style="margin-top:8px">
            <button class="primary" type="button" (click)="savePreview(p)">{{ hasProfile() ? 'Replace my profile' : 'Save as my profile' }}</button>
            <button class="secondary" type="button" (click)="preview.set(null)">Cancel</button>
          </div>
        </div>
      }
    </div>

    <app-bank-panel />

    <div class="card">
      <h2>Edit as JSON (advanced)</h2>
      <p class="hint">Every detail of your profile, including <code>preferences</code> (target roles, locations) and
        <code>resume_settings</code> (bullets per role, focus themes).
        <button class="secondary tight" type="button" (click)="loadExample()">Load fictional example</button></p>
      <textarea class="code" id="profile-json" aria-label="Profile JSON" spellcheck="false" [(ngModel)]="json"></textarea>
      <button class="primary" type="button" (click)="save()">Save profile</button>
      <app-msg [msg]="msg()" />
    </div>
  `,
})
export class ProfilePage implements OnInit {
  private api = inject(Api);

  protected readonly json = signal('');
  protected readonly msg = signal<Msg | null>(null);
  protected readonly uploadMsg = signal<Msg | null>(null);
  protected readonly hasProfile = signal(false);
  protected readonly summary = signal('');
  protected readonly resumeText = signal('');
  protected readonly busy = signal(false);
  protected readonly preview = signal<ResumeImport | null>(null);

  async ngOnInit() {
    await this.load();
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

  protected bulletCount(p: ResumeImport): number {
    return (p.profile.experience || []).reduce((n: number, e: any) => n + (e.highlights || []).length, 0);
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
      const data: Record<string, unknown> = { ...p.profile };
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
