export type VoiceTranscriptionOutcome = 'submitted' | 'empty' | 'failed' | 'exit';

export const shouldRestoreVoiceListening = (
	outcome: VoiceTranscriptionOutcome,
	voiceModeOpen: boolean
): boolean => voiceModeOpen && outcome !== 'submitted' && outcome !== 'exit';
