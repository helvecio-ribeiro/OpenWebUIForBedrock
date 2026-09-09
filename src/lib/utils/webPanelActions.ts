const CHALLENGE_SECTION_TITLES: Record<string, string> = {
	verdict: 'Verdict',
	explanation: 'Explanation',
	'individual factual claims': 'Individual Factual Claims',
	'supporting information': 'Supporting Information',
	'how to verify': 'How to verify'
};

const normalizeVerdict = (value: string) => {
	const verdict = value
		.trim()
		.replace(/[.*_#]+$/g, '')
		.toLowerCase();
	return (
		{
			true: 'True',
			false: 'False',
			'mostly true': 'Mostly true',
			'mostly false': 'Mostly false'
		}[verdict] ?? value.trim()
	);
};

/** Normalize model variations into the documented Challenge Text Markdown format. */
export const normalizeChallengeResult = (content: string) => {
	const normalized = content.split('\n').map((line) => {
		const match = line.match(
			/^\s*(?:#{1,6}\s*)?(?:\*\*)?(verdict|explanation|individual factual claims|supporting information|how to verify)\s*:?[ \t]*(?:\*\*)?\s*:?[ \t]*(.*)$/i
		);
		if (!match) return line.trimEnd();

		const key = match[1].toLowerCase();
		const title = CHALLENGE_SECTION_TITLES[key];
		const value = key === 'verdict' ? normalizeVerdict(match[2]) : match[2].trim();
		return `**${title}:**${value ? ` ${value}` : ''}`;
	});

	return normalized
		.join('\n')
		.replace(/\n{3,}/g, '\n\n')
		.trim();
};
