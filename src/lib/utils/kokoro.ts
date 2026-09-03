import { get } from 'svelte/store';

import { TTSWorker } from '$lib/stores';
import { KokoroWorker } from '$lib/workers/KokoroWorker';

export type KokoroDtype = 'fp32' | 'fp16' | 'q8' | 'q4' | 'q4f16';
export type KokoroDevice = 'webgpu' | 'wasm';

type NavigatorWithGpu = Navigator & {
	gpu?: { requestAdapter: () => Promise<unknown | null> };
};

let initialization: Promise<KokoroWorker> | null = null;

export const resolveKokoroVoiceId = (voice?: string, fallback = 'bf_emma') =>
	voice && /^[a-z][fm]_[a-z0-9_]+$/i.test(voice) ? voice : fallback;

export const getKokoroDevice = async (): Promise<KokoroDevice> => {
	const gpu = (navigator as NavigatorWithGpu).gpu;
	if (!gpu) return 'wasm';

	try {
		return (await gpu.requestAdapter()) ? 'webgpu' : 'wasm';
	} catch (error) {
		console.warn('WebGPU is unavailable; using WASM for Kokoro.', error);
		return 'wasm';
	}
};

export const getOrInitKokoroWorker = async (
	dtype: KokoroDtype = 'q8',
	device: 'auto' | KokoroDevice = 'auto',
	voice?: string
) => {
	const existing = get(TTSWorker);
	if (existing?.isInitialized()) {
		if (voice) await existing.warmup(voice);
		return existing;
	}

	if (!initialization) {
		initialization = (async () => {
			const worker = new KokoroWorker(dtype, device);
			try {
				await worker.init();
				TTSWorker.set(worker);
				return worker;
			} catch (error) {
				worker.terminate(error instanceof Error ? error : new Error(`${error}`));
				throw error;
			} finally {
				initialization = null;
			}
		})();
	}

	const worker = await initialization;
	if (voice) await worker.warmup(voice);
	return worker;
};
