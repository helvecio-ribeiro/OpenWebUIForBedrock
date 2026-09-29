import { describe, expect, it } from 'vitest';

import {
	isMatchingWebPanelCapture,
	normalizeChallengeResult,
	withWebPanelTemporalContext
} from './webPanelActions';

describe('isMatchingWebPanelCapture', () => {
	const pending = { requestId: 'capture-1', generation: 4 };

	it('accepts only the expected response from the current navigation generation', () => {
		expect(
			isMatchingWebPanelCapture(
				pending,
				{ type: 'document-captured', requestId: 'capture-1', generation: 4 },
				4
			)
		).toBe(true);
	});

	it.each([
		[null, { type: 'document-captured', requestId: 'capture-1', generation: 4 }, 4],
		[pending, { type: 'selection-action', requestId: 'capture-1', generation: 4 }, 4],
		[pending, { type: 'document-captured', requestId: 'other', generation: 4 }, 4],
		[pending, { type: 'document-captured', requestId: 'capture-1', generation: 3 }, 4],
		[pending, { type: 'document-captured', requestId: 'capture-1', generation: 4 }, 5]
	])('rejects missing, unsolicited, or stale captures', (candidate, message, generation) => {
		expect(isMatchingWebPanelCapture(candidate, message, generation)).toBe(false);
	});
});

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
