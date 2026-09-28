import { describe, expect, it } from 'vitest';

import { normalizeChallengeResult, withWebPanelTemporalContext } from './webPanelActions';

describe('withWebPanelTemporalContext', () => {
	it('adds a deterministic current-date reference to every action instruction', () => {
		const result = withWebPanelTemporalContext(
			'Fact-check the selected text.',
			new Date('2026-09-22T22:30:45.000Z'),
			'America/Mexico_City'
		);

		expect(result).toContain('Current date: 2026-09-22');
		expect(result).toContain('Current local datetime: 2026-09-22 16:30:45');
		expect(result).toContain('User timezone: America/Mexico_City');
		expect(result).toContain('Current UTC datetime: 2026-09-22T22:30:45.000Z');
		expect(result).toContain('Do not substitute a model training cutoff');
		expect(result).toMatch(/Fact-check the selected text\.$/);
	});
});

describe('normalizeChallengeResult', () => {
	it('bolds and canonicalizes every challenge section title', () => {
		const result = normalizeChallengeResult(`### Verdict: mostly TRUE

Explanation: Some context.

**Individual factual claims:**
1. First claim

Supporting Information:
1. Evidence

## How To Verify
1. Check the source`);

		expect(result).toBe(`**Verdict:** Mostly true

**Explanation:** Some context.

**Individual Factual Claims:**
1. First claim

**Supporting Information:**
1. Evidence

**How to verify:**
1. Check the source`);
	});
});
