import { describe, expect, it } from 'vitest';

import { getSpeechText, isVoiceExitCommand, VOICE_EXIT_ACKNOWLEDGEMENT } from './tts';

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
