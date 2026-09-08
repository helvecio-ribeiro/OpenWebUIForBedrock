const sentenceEnd = /[.!?]$/;

const finishSentence = (value: string) => {
	const text = value.trim().replace(/[,:;]+$/, '');
	return text && !sentenceEnd.test(text) ? `${text}.` : text;
};

const cleanInlineMarkdown = (value: string) =>
	value
		.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
		.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
		.replace(/(?:\*\*|__)(.*?)(?:\*\*|__)/g, '$1')
		.replace(/(?:[*_])(.*?)(?:[*_])/g, '$1')
		.replace(/~~(.*?)~~/g, '$1')
		.replace(/`([^`]+)`/g, '$1')
		.replace(/<[^>]+>/g, '')
		.trim();

const spokenField = (label: string, value: string) => {
	const normalizedValue = value
		.replace(/\s+(?:-|–|—)\s+/g, ' to ')
		.replace(/\s+/g, ' ')
		.trim();

	switch (label.toLowerCase()) {
		case 'date':
			return `On ${normalizedValue}`;
		case 'time':
			return `From ${normalizedValue}`;
		case 'location':
			return `At ${normalizedValue}`;
		case 'description':
		case 'details':
			return normalizedValue;
		default:
			return `${label}, ${normalizedValue}`;
	}
};

/**
 * Produce a speech-only projection of visually structured assistant text.
 * The original Markdown remains untouched for display and persistence.
 */
export const getSpeechText = (content: string): string => {
	const withoutNonSpeechBlocks = content
		.replace(/<details[^>]*>[\s\S]*?<\/details>/gi, '')
		.replace(/```[\s\S]*?```/g, '')
		.replace(/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/gm, '');

	const sentences: string[] = [];
	for (const rawLine of withoutNonSpeechBlocks.split(/\n+/)) {
		let line = cleanInlineMarkdown(rawLine)
			.replace(/^#{1,6}\s+/, '')
			.replace(/^\s*>+\s*/, '')
			.trim();
		if (!line) continue;

		if (line.includes('|')) {
			line = line
				.split('|')
				.map((cell) => cell.trim())
				.filter(Boolean)
				.join(', ');
		}

		line = line
			.replace(/^\s*(?:[-*+]\s+|\d+[.)]\s+)/, '')
			.replace(/\s+/g, ' ')
			.trim();
		if (!line) continue;

		const field = line.match(/^([A-Za-z][A-Za-z ]{1,30}):\s*(.+)$/);
		if (field && sentences.length) {
			sentences.push(finishSentence(spokenField(field[1], field[2])));
		} else {
			sentences.push(finishSentence(line));
		}
	}

	return sentences.join(' ').replace(/\s+/g, ' ').trim();
};

const VOICE_EXIT_COMMANDS = new Set([
	'exit',
	'exit voice mode',
	'close voice mode',
	'end voice conversation',
	'stop listening'
]);

export const VOICE_EXIT_ACKNOWLEDGEMENT = 'Goodbye.';

/** Match only a complete, deliberate exit utterance, never a phrase embedded in a question. */
export const isVoiceExitCommand = (transcript: string): boolean => {
	const normalized = transcript
		.toLocaleLowerCase()
		.replace(/[.!?,;:]+$/g, '')
		.replace(/\s+/g, ' ')
		.trim();
	return VOICE_EXIT_COMMANDS.has(normalized);
};
