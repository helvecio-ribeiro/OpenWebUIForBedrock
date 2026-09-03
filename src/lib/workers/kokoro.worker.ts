import { KokoroTTS } from 'kokoro-js';

let tts: Awaited<ReturnType<typeof KokoroTTS.from_pretrained>> | null = null;
let isInitialized = false; // Flag to track initialization status
const DEFAULT_MODEL_ID = 'onnx-community/Kokoro-82M-v1.0-ONNX'; // Default model

const getKokoroDevice = async () => {
	const gpu = (
		navigator as Navigator & {
			gpu?: { requestAdapter: () => Promise<unknown | null> };
		}
	).gpu;
	if (!gpu) return 'wasm';
	try {
		return (await gpu.requestAdapter()) ? 'webgpu' : 'wasm';
	} catch {
		return 'wasm';
	}
};

self.onmessage = async (event) => {
	const { type, payload } = event.data;

	if (type === 'init') {
		let { model_id, dtype, device: configuredDevice = 'auto' } = payload;
		model_id = model_id || DEFAULT_MODEL_ID; // Use default model if none provided

		self.postMessage({ status: 'init:start' });

		try {
			const device = configuredDevice === 'auto' ? await getKokoroDevice() : configuredDevice;
			const progress_callback = (progress) => {
				self.postMessage({ status: 'init:progress', progress });
			};
			try {
				tts = await KokoroTTS.from_pretrained(model_id, { dtype, device, progress_callback });
			} catch (error) {
				if (device !== 'webgpu') throw error;
				console.warn('Kokoro WebGPU initialization failed; retrying with WASM.', error);
				tts = await KokoroTTS.from_pretrained(model_id, {
					dtype,
					device: 'wasm',
					progress_callback
				});
			}
			isInitialized = true; // Mark as initialized after successful loading
			self.postMessage({ status: 'init:complete' });
		} catch (error) {
			isInitialized = false; // Ensure it's marked as false on failure
			self.postMessage({
				status: 'init:error',
				error: error instanceof Error ? error.message : `${error}`
			});
		}
	}

	if (type === 'generate') {
		if (!isInitialized || !tts) {
			// Ensure model is initialized
			self.postMessage({ status: 'generate:error', error: 'TTS model not initialized' });
			return;
		}

		const { text, voice } = payload;
		self.postMessage({ status: 'generate:start', voice, textLength: text.length });

		try {
			const rawAudio = await tts.generate(text, { voice });
			const blob = await rawAudio.toBlob();
			const blobUrl = URL.createObjectURL(blob);
			self.postMessage({ status: 'generate:complete', audioUrl: blobUrl });
		} catch (error) {
			self.postMessage({
				status: 'generate:error',
				error: error instanceof Error ? error.message : `${error}`
			});
		}
	}

	if (type === 'status') {
		// Respond with the current initialization status
		self.postMessage({ status: 'status:check', initialized: isInitialized });
	}
};
