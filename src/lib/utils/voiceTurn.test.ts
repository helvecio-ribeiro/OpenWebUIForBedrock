import { describe, expect, it } from 'vitest';

import { shouldRestoreVoiceListening } from './voiceTurn';

describe('voice turn recovery', () => {
	it('keeps listening stopped after an accepted submission', () => {
		expect(shouldRestoreVoiceListening('submitted', true)).toBe(false);
	});

	it('restores listening when transcription is empty or fails', () => {
		expect(shouldRestoreVoiceListening('empty', true)).toBe(true);
		expect(shouldRestoreVoiceListening('failed', true)).toBe(true);
	});

	it('does not restart while exiting or after Voice Mode closes', () => {
		expect(shouldRestoreVoiceListening('exit', true)).toBe(false);
		expect(shouldRestoreVoiceListening('failed', false)).toBe(false);
	});
});
