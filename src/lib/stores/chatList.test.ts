import { get } from 'svelte/store';
import { beforeEach, describe, expect, it } from 'vitest';

import {
	chatSelectionMode,
	resetChatSelection,
	selectedChatIds,
	toggleChatSelection
} from './chatList';

describe('chat batch selection', () => {
	beforeEach(() => resetChatSelection());

	it('toggles independent chat IDs', () => {
		chatSelectionMode.set(true);
		toggleChatSelection('chat-1');
		toggleChatSelection('chat-2');
		toggleChatSelection('chat-1');

		expect(get(selectedChatIds)).toEqual(['chat-2']);
		expect(get(chatSelectionMode)).toBe(true);
	});

	it('resets selection and exits selection mode', () => {
		chatSelectionMode.set(true);
		toggleChatSelection('chat-1');

		resetChatSelection();

		expect(get(selectedChatIds)).toEqual([]);
		expect(get(chatSelectionMode)).toBe(false);
	});
});
