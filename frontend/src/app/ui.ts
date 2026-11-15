import { Component, input } from '@angular/core';

/** A status message under a form. kind: ok (green), bad (red), info (blue). */
export interface Msg { kind: 'ok' | 'bad' | 'info'; text: string; }

@Component({
  selector: 'app-msg',
  template: `@if (msg(); as m) { <div class="msg" [class]="'msg ' + m.kind" role="status">{{ m.text }}</div> }`,
})
export class MsgBox {
  readonly msg = input<Msg | null>(null);
}

/** Green / orange / red chip for a preference check ("strong", "partial", "none", "yes", "no", …). */
@Component({
  selector: 'app-fit',
  template: `<span class="chip" [class.ok]="level() === 'strong' || level() === 'yes'"
                   [class.bad]="level() === 'none' || level() === 'no'"
                   [class.warn]="!['strong', 'yes', 'none', 'no'].includes(level())">{{ text() }}</span>`,
})
export class FitChip {
  readonly level = input.required<string>();
  readonly text = input.required<string>();
}
