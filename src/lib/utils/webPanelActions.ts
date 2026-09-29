const CHALLENGE_SECTION_TITLES: Record<string, string> = {
	verdict: 'Verdict',
	explanation: 'Explanation',
	'individual factual claims': 'Individual Factual Claims',
	'supporting information': 'Supporting Information',
	'how to verify': 'How to verify'
};

export type PendingWebPanelCapture = {
	requestId: string;
	generation: number;
};

/** Reject stale, unsolicited, and cross-navigation Browser capture responses. */
export const isMatchingWebPanelCapture = (
	pending: PendingWebPanelCapture | null,
	message: unknown,
	currentGeneration: number
) => {
	if (!pending || !message || typeof message !== 'object') return false;
	const candidate = message as { type?: unknown; requestId?: unknown; generation?: unknown };
	return (
		candidate.type === 'document-captured' &&
		typeof candidate.requestId === 'string' &&
		candidate.requestId === pending.requestId &&
		candidate.generation === pending.generation &&
		pending.generation === currentGeneration
	);
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

const localDateTimeParts = (now: Date, timeZone: string) => {
	const parts = new Intl.DateTimeFormat('en-US', {
		timeZone,
		year: 'numeric',
		month: '2-digit',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit',
		hourCycle: 'h23'
	}).formatToParts(now);
	const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
	return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
};

/** Add an authoritative, request-time temporal reference to every Browser Action. */
export const withWebPanelTemporalContext = (
	instruction: string,
	now = new Date(),
	timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
) => {
	const localDateTime = localDateTimeParts(now, timeZone);
	const localDate = localDateTime.slice(0, 10);
	return `Temporal context (authoritative for this Browser Action):
- Current date: ${localDate}
- Current local datetime: ${localDateTime}
- User timezone: ${timeZone}
- Current UTC datetime: ${now.toISOString()}

Use this temporal context for every date-sensitive statement, even when the action does not explicitly mention dates. Evaluate whether events are past, present, or future relative to this date. Do not substitute a model training cutoff or an assumed reference date. If current external facts are required but unavailable, state that limitation instead of treating old knowledge as current.

${instruction}`;
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
