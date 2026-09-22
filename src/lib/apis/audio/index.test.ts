import { afterEach, describe, expect, it, vi } from 'vitest';

import { synthesizeOpenAISpeech } from './index';

describe('synthesizeOpenAISpeech', () => {
	afterEach(() => vi.unstubAllGlobals());

	it('sends browser preference and full-response language context', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('audio', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await synthesizeOpenAISpeech('token', 'bf_emma', 'Sí.', undefined, {
			preferredLanguage: 'es',
			languageContext: 'Sí. Su cita es mañana.'
		});

		const [, init] = fetchMock.mock.calls[0];
		expect(JSON.parse(init.body)).toEqual({
			input: 'Sí.',
			voice: 'bf_emma',
			preferred_language: 'es',
			language_context: 'Sí. Su cita es mañana.'
		});
	});

	it('omits language routing hints when none are supplied', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('audio', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await synthesizeOpenAISpeech('token', 'bf_emma', 'Ready.');

		const [, init] = fetchMock.mock.calls[0];
		expect(JSON.parse(init.body)).toEqual({ input: 'Ready.', voice: 'bf_emma' });
	});
});
