import { TestBed } from '@angular/core/testing';

import { setup, settle, text } from '../testing/setup';
import { ResumeCard } from './resume-card';

describe('ResumeCard (review details)', () => {
  it('maps each requirement to the resume line that shows it, and lists what the keyword check left out', async () => {
    const resume = {
      id: 21, application_id: 7, kind: 'resume', question: '', version: 1, source: 'ai', created_at: '2026-11-14T10:00:00',
      content: JSON.stringify({ summary: 'Quality engineer (fictional).', skills: ['Python'], experience: [] }),
      review: {
        warnings: [], notes: [], tailoring: {}, keywords_added: [],
        ats: { score: 80, score_before: 70, max_honest_score: 90, compared: 2, gaps: [], missing_but_evidenced: [],
               matched: [{ keyword: 'Python', weight: 2, evidence: 'Built pytest suites in Python (fictional).' }],
               not_scored: ['curiosity', 'hybrid working policy'] },
        requirements_map: [
          { requirement: 'Python and SQL', kind: 'required', category: 'technical', strength: 'partial',
            found: ['python'], missing: ['sql'], where: null, not_printed: ['EX07'],
            on_resume: [{ id: 'EX01', owner: 'Example Corp', where: 'Example Corp, bullet 2',
                          line: 'Built pytest suites in Python (fictional).' }] },
          { requirement: 'Curiosity and initiative', kind: 'required', category: 'soft skill',
            strength: 'not shown', found: [], missing: ['curiosity'], where: null, on_resume: [], not_printed: [] },
        ],
      },
    };
    setup([ResumeCard]);
    const fixture = TestBed.createComponent(ResumeCard);
    fixture.componentRef.setInput('versions', [resume]);
    fixture.detectChanges();
    await settle(fixture);
    const map = text(fixture);
    expect(map).toContain('Job-description keyword coverage: 80%');
    expect(map).toContain('Requirements → your experience → where it is on the resume');
    expect(map).toContain('0 met · 1 partly · 0 not met · 1 soft skills');
    expect(map).toContain('Example Corp, bullet 2');
    expect(map).toContain('Cut to fit two pages: EX07');
    expect(map).toContain('✗ sql');
    expect(map).toContain('Not scored (people skills and ways of working, not technical keywords): curiosity · hybrid working policy');
  });
});
