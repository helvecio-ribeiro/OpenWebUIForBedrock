import WorkerInstance from '$lib/workers/kokoro.worker?worker';

export class KokoroWorker {
	private static readonly INIT_TIMEOUT_MS = 180_000;
	private static readonly GENERATE_TIMEOUT_MS = 45_000;
	private worker: Worker | null = null;
	private initialized: boolean = false;
	private dtype: string;
	private device: 'auto' | 'webgpu' | 'wasm';
	private requestQueue: Array<{
		text: string;
		voice: string;
		resolve: (value: string) => void;
		reject: (reason: any) => void;
	}> = [];
	private processing = false; // To track if a request is being processed
	private generationTimeout: ReturnType<typeof setTimeout> | null = null;
	private warmups = new Map<string, Promise<void>>();

	constructor(dtype: string = 'fp32', device: 'auto' | 'webgpu' | 'wasm' = 'auto') {
		this.dtype = dtype;
		this.device = device;
	}

	public isInitialized() {
		return this.initialized;
	}

	public async init() {
		if (this.worker) {
			console.warn('KokoroWorker is already initialized.');
			return;
		}

		this.worker = new WorkerInstance();

		// Handle worker messages
		this.worker.onmessage = (event) => {
			const { status, error, audioUrl, progress, voice, textLength } = event.data;

			if (status === 'init:complete') {
				console.info('Kokoro worker initialized.');
				this.initialized = true;
			} else if (status === 'init:error') {
				console.error('Kokoro worker initialization failed:', error);
				this.initialized = false;
			} else if (status === 'init:progress') {
				console.debug('Kokoro initialization progress:', progress);
			} else if (status === 'generate:start') {
				console.info(`Kokoro synthesis started (${voice}, ${textLength} characters).`);
			} else if (status === 'generate:complete') {
				this.clearGenerationTimeout();
				// Resolve promise from queue
				const request = this.requestQueue.shift();
				if (request) {
					console.info(`Kokoro synthesis completed (${request.voice}).`);
					request.resolve(audioUrl);
					this.processNextRequest(); // Process next request in queue
				}
			} else if (status === 'generate:error') {
				this.clearGenerationTimeout();
				const request = this.requestQueue.shift();
				if (request) {
					request.reject(new Error(error));
					this.processNextRequest(); // Continue processing next in queue
				}
			}
		};

		return new Promise<void>((resolve, reject) => {
			let settled = false;
			const finish = (error?: Error) => {
				if (settled) return;
				settled = true;
				clearTimeout(timeout);
				this.worker?.removeEventListener('message', handleMessage);
				this.worker?.removeEventListener('error', handleError);
				if (error) reject(error);
				else resolve();
			};
			const handleError = (event: ErrorEvent) => {
				console.error('Kokoro worker crashed:', event);
				finish(new Error(event.message || 'Kokoro worker crashed'));
			};
			const timeout = setTimeout(() => {
				finish(new Error('Kokoro initialization timed out after 180 seconds'));
			}, KokoroWorker.INIT_TIMEOUT_MS);

			this.worker!.postMessage({
				type: 'init',
				payload: { dtype: this.dtype, device: this.device }
			});

			const handleMessage = (event: MessageEvent) => {
				if (event.data.status === 'init:complete') {
					this.initialized = true;
					finish();
				} else if (event.data.status === 'init:error') {
					finish(new Error(event.data.error));
				}
			};

			this.worker!.addEventListener('message', handleMessage);
			this.worker!.addEventListener('error', handleError);
		});
	}

	public async generate({ text, voice }: { text: string; voice: string }): Promise<string> {
		if (!this.initialized || !this.worker) {
			throw new Error('KokoroTTS Worker is not initialized yet.');
		}

		return new Promise<string>((resolve, reject) => {
			this.requestQueue.push({ text, voice, resolve, reject });
			if (!this.processing) {
				this.processNextRequest();
			}
		});
	}

	public async warmup(voice: string): Promise<void> {
		const current = this.warmups.get(voice);
		if (current) return current;

		const warmup = (async () => {
			console.info(`Warming Kokoro voice ${voice}.`);
			const audioUrl = await this.generate({ text: 'Ready.', voice });
			URL.revokeObjectURL(audioUrl);
			console.info(`Kokoro voice ${voice} is ready.`);
		})().catch((error) => {
			this.warmups.delete(voice);
			throw error;
		});

		this.warmups.set(voice, warmup);
		return warmup;
	}

	private processNextRequest() {
		if (this.requestQueue.length === 0) {
			this.processing = false;
			return;
		}

		this.processing = true;
		const { text, voice } = this.requestQueue[0]; // Get first request but don't remove yet
		this.worker!.postMessage({ type: 'generate', payload: { text, voice } });
		this.generationTimeout = setTimeout(() => {
			const error = new Error(
				`Kokoro synthesis timed out after ${KokoroWorker.GENERATE_TIMEOUT_MS / 1000} seconds (${voice}).`
			);
			console.error(error);
			this.terminate(error);
		}, KokoroWorker.GENERATE_TIMEOUT_MS);
	}

	private clearGenerationTimeout() {
		if (this.generationTimeout) {
			clearTimeout(this.generationTimeout);
			this.generationTimeout = null;
		}
	}

	public terminate(reason = new Error('Kokoro worker terminated')) {
		this.clearGenerationTimeout();
		for (const request of this.requestQueue) request.reject(reason);
		this.requestQueue = [];
		this.warmups.clear();
		if (this.worker) {
			this.worker.terminate();
			this.worker = null;
			this.initialized = false;
			this.processing = false;
		}
	}
}
