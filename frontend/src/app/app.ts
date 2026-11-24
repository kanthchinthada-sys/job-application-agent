import { Component, inject, OnInit, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Api } from './api';
import { GO_TO_LOGIN } from './auth.interceptor';
import { Me } from './models';

/** Forget everything this browser tab kept for the signed-in account (searches, filters, scroll positions). */
export function clearSessionState() {
  try {
    Object.keys(sessionStorage).filter(k => k.startsWith('job-agent.') || k === 'find-jobs-state-v1')
      .forEach(k => sessionStorage.removeItem(k));
  } catch { /* storage unavailable */ }
}

/** The shell: sidebar navigation, the signed-in account, the local AI status, and the router outlet. */
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit {
  private api = inject(Api);
  private goToLogin = inject(GO_TO_LOGIN);

  protected readonly ai = signal<{ ok: boolean; model: string; reachable: boolean; available: boolean } | null>(null);
  protected readonly aiText = signal('Checking AI…');
  protected readonly me = signal<Me | null>(null);
  protected readonly username = signal<string | null>(null);
  protected readonly signOutShown = signal(false);
  protected readonly navOpen = signal(false);

  async ngOnInit() {
    this.api.health().then(h => { if (h.password_protection || h.accounts) this.signOutShown.set(true); }).catch(() => {});
    this.api.me().then(m => { this.me.set(m); this.username.set(m.username); if (m.accounts) this.signOutShown.set(true); })
      .catch(() => {});
    await this.checkAi();
  }

  async checkAi() {
    this.aiText.set('Checking AI…');
    try {
      const s = await this.api.aiStatus();
      const ok = s.reachable && s.model_available;
      this.ai.set({ ok, model: s.model, reachable: s.reachable, available: s.model_available });
      this.aiText.set(ok ? 'AI ready' : s.reachable ? 'AI: model missing' : 'AI offline');
    } catch {
      this.ai.set(null);
      this.aiText.set('AI status unknown');
    }
  }

  protected aiDetail(): string {
    const a = this.ai();
    if (!a) return 'unknown';
    return a.ok ? 'running on this computer' : a.reachable ? `Ollama is running, but ${a.model} isn't downloaded`
      : 'Ollama is not reachable';
  }

  async signOut() {
    await this.api.logout().catch(() => {});
    clearSessionState();                  // nothing of this account stays in the browser for the next one
    this.goToLogin();
  }
}
