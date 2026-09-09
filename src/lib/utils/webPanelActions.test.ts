import { describe, expect, it } from 'vitest';

import { normalizeChallengeResult } from './webPanelActions';

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
