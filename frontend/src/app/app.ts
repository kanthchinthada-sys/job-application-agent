import { Component, inject, OnInit, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Api } from './api';
import { GO_TO_LOGIN } from './auth.interceptor';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit {
  private api = inject(Api);
  private goToLogin = inject(GO_TO_LOGIN);

  protected readonly aiText = signal('Checking AI…');
  protected readonly passwordOn = signal(false);

  async ngOnInit() {
    this.api.health().then(h => this.passwordOn.set(h.password_protection)).catch(() => {});
    try {
      const s = await this.api.aiStatus();
      this.aiText.set(s.reachable && s.model_available ? `AI ready · ${s.model}`
        : s.reachable ? `AI: model ${s.model} not downloaded` : 'AI offline (open the Ollama app)');
    } catch {
      this.aiText.set('AI status unknown');
    }
  }

  async signOut() {
    await this.api.logout().catch(() => {});
    this.goToLogin();
  }
}
