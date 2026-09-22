import { describe, expect, it } from 'vitest';

import {
	appendTTSLanguageContext,
	getPreferredTTSLanguage,
	getSpeechText,
	isVoiceExitCommand,
	normalizeTTSLanguage,
	VOICE_EXIT_ACKNOWLEDGEMENT
} from './tts';

describe('TTS locale defaults', () => {
	it('normalizes English, Spanish, and Portuguese browser locales', () => {
		expect(normalizeTTSLanguage('en-US')).toBe('en');
		expect(normalizeTTSLanguage('es_MX')).toBe('es');
		expect(normalizeTTSLanguage('pt-BR')).toBe('pt');
		expect(normalizeTTSLanguage('fr-FR')).toBeNull();
	});

	it('prefers the interface locale and then the browser language list', () => {
		expect(getPreferredTTSLanguage('es-ES', ['en-US'])).toBe('es');
		expect(getPreferredTTSLanguage('pt-BR', ['en-US'])).toBe('pt');
		expect(getPreferredTTSLanguage('fr-FR', ['es-MX', 'en-US'])).toBe('es');
		expect(getPreferredTTSLanguage(null, ['fr-FR'])).toBe('en');
	});
});

describe('streaming TTS language context', () => {
	it('keeps earlier language evidence when a later fragment is ambiguous', () => {
		const first = appendTTSLanguageContext('', 'Claro, posso ajudar em português do Brasil.');
		const second = appendTTSLanguageContext(
			first,
			'Se tiver alguma pergunta ou precisar de assistência, sinta-se à vontade para perguntar.'
		);

		expect(second).toBe(
			'Claro, posso ajudar em português do Brasil. Se tiver alguma pergunta ou precisar de assistência, sinta-se à vontade para perguntar.'
		);
	});

	it('caps accumulated context while retaining the newest fragment', () => {
		expect(appendTTSLanguageContext('12345', '67890', 8)).toBe('45 67890');
	});
});

describe('getSpeechText', () => {
	it('turns a calendar list into natural speech without changing its facts', () => {
		const display = `Here is the list of your appointments for this week:

1. **Appointment with Dr. Anna**
   - Date: September 8, 2026
   - Time: 4:00 PM - 4:30 PM
   - Location: Main clinic`;

		expect(getSpeechText(display)).toBe(
			'Here is the list of your appointments for this week. Appointment with Dr. Anna. On September 8, 2026. From 4:00 PM to 4:30 PM. At Main clinic.'
		);
	});

	it('removes code and markdown while preserving ordinary prose', () => {
		expect(getSpeechText('## Result\nThe operation **worked**.\n```sh\nsecret command\n```')).toBe(
			'Result. The operation worked.'
		);
	});

	it('converts table rows into speakable text', () => {
		const table = '| Name | Time |\n| --- | --- |\n| Dentist | 10 AM |';
		expect(getSpeechText(table)).toBe('Name, Time. Dentist, 10 AM.');
	});
});

describe('isVoiceExitCommand', () => {
	it('uses a short deterministic acknowledgement without asking the model', () => {
		expect(VOICE_EXIT_ACKNOWLEDGEMENT).toBe('Goodbye.');
	});
	it.each(['exit', 'Exit.', 'exit voice mode', 'close voice mode!', 'stop listening'])(
		'matches the complete command %s',
		(command) => expect(isVoiceExitCommand(command)).toBe(true)
	);

	it.each(['how do I exit voice mode', 'do not stop listening', 'exit the application'])(
		'does not match an embedded or different request: %s',
		(command) => expect(isVoiceExitCommand(command)).toBe(false)
	);
});
