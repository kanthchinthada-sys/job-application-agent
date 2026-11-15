import { TestBed } from '@angular/core/testing';

import { button, settle, setup, text } from '../testing/setup';
import { BankPanel } from './bank-panel';

const REC = (id: string, status: string, extra: object = {}) => ({
  id, text: `Fictional record ${id}.`, owner: 'Example Corp (fictional)', owner_kind: 'experience', group: null,
  status, status_note: null, caveat: null, ...extra,
});

describe('BankPanel', () => {
  async function render(records: object[]) {
    const ctx = setup([BankPanel]);
    const fixture = TestBed.createComponent(BankPanel);
    fixture.detectChanges();
    ctx.http.expectOne('/profile').flush({ full_name: 'Jordan (fictional)', experience_bank: records });
    await settle(fixture);
    return { ...ctx, fixture };
  }

  it('imports a bank and shows where records went, what was removed and what is missing', async () => {
    const { fixture, http } = await render([]);
    const box = fixture.nativeElement.querySelector('textarea') as HTMLTextAreaElement;
    box.value = 'CH01: Fictional record.';
    box.dispatchEvent(new Event('input'));
    button(fixture, 'Import records').click();
    http.expectOne('/profile/bank').flush({
      report: { imported: 2, by_owner: { 'Example Corp (fictional)': 2 }, duplicates_removed: ['TL01'],
                rerouted: ['SD10: Contoso -> Project'], missing_ids: ['FDE01'], notes_kept_as_caveats: ['CH24'],
                labels: [], status_counts: { verified: 1, not_true: 1 } },
      records: [REC('CH01', 'verified'), REC('AI03', 'not_true', { status_note: 'Not in the project.' })],
    });
    await settle(fixture);
    const page = text(fixture);
    expect(page).toContain('2 records imported');
    expect(page).toContain('Duplicates removed: TL01');
    expect(page).toContain('Missing from your bank (not filled in): FDE01');
    expect(page).toContain('Not in the project.');
  });

  it('lets you confirm a record', async () => {
    const { fixture, http } = await render([REC('QM01', 'unverified')]);
    const select = fixture.nativeElement.querySelector('select[aria-label="Status of QM01"]') as HTMLSelectElement;
    select.value = 'verified';
    select.dispatchEvent(new Event('change'));
    const req = http.expectOne('/profile/bank/QM01');
    expect(req.request.body.status).toBe('verified');
    req.flush(REC('QM01', 'verified', { status_note: 'Confirmed by you.' }));
    await settle(fixture);
    expect(text(fixture)).toContain('Confirmed by you.');
  });
});
