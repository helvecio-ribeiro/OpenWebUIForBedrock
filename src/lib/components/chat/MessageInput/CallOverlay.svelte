<script lang="ts">
	import { config, models, settings, showCallOverlay, TTSWorker } from '$lib/stores';
	import { onMount, tick, getContext, onDestroy, createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	import { blobToFile } from '$lib/utils';
	import { generateEmoji } from '$lib/apis';
	import { synthesizeOpenAISpeech, transcribeAudio } from '$lib/apis/audio';
	import { getOrInitKokoroWorker, resolveKokoroVoiceId } from '$lib/utils/kokoro';
	import { isVoiceExitCommand, VOICE_EXIT_ACKNOWLEDGEMENT } from '$lib/utils/tts';

	import { toast } from 'svelte-sonner';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import VideoInputMenu from './CallOverlay/VideoInputMenu.svelte';
	import AudioWaveform from './CallOverlay/AudioWaveform.svelte';
	import { KokoroWorker } from '$lib/workers/KokoroWorker';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const i18n = getContext('i18n');

	export let eventTarget: EventTarget;
	export let submitPrompt: Function;
	export let stopResponse: Function;
	export let files;
	export let chatId;
	export let modelId;

	let wakeLock = null;

	let model = null;

	let loading = false;
	let confirmed = false;
	let interrupted = false;
	let assistantSpeaking = false;
	let ttsPlaying = false;
	let playbackLevels = Array(5).fill(0.1);
	let muted = false;
	let exitAfterPlayback = false;

	let emoji = null;
	let camera = false;
	let cameraStream = null;

	let chatStreaming = false;
	let rmsLevel = 0;
	let hasStartedSpeaking = false;
	let mediaRecorder;
	let audioStream = null;
	let audioChunks = [];
	let audioPreRollChunks = [];
	let audioContainerHeader: Blob | null = null;
	let microphoneAudioContext: AudioContext | null = null;
	let microphoneAnimationFrame: number | null = null;

	let videoInputDevices = [];
	let selectedVideoInputDeviceId = null;

	const getVideoInputDevices = async () => {
		const devices = await navigator.mediaDevices.enumerateDevices();
		videoInputDevices = devices.filter((device) => device.kind === 'videoinput');

		if (!!navigator.mediaDevices.getDisplayMedia) {
			videoInputDevices = [
				...videoInputDevices,
				{
					deviceId: 'screen',
					label: 'Screen Share'
				}
			];
		}

		console.log(videoInputDevices);
		if (selectedVideoInputDeviceId === null && videoInputDevices.length > 0) {
			const savedDeviceId = localStorage.getItem('selectedVideoInputDeviceId');
			if (savedDeviceId && videoInputDevices.some((d) => d.deviceId === savedDeviceId)) {
				selectedVideoInputDeviceId = savedDeviceId;
			} else {
				selectedVideoInputDeviceId = videoInputDevices[0].deviceId;
			}
		}
	};

	const startCamera = async () => {
		await getVideoInputDevices();

		if (cameraStream === null) {
			camera = true;
			await tick();
			try {
				await startVideoStream();
			} catch (err) {
				console.error('Error accessing webcam: ', err);
			}
		}
	};

	const startVideoStream = async () => {
		const video = document.getElementById('camera-feed');
		if (video) {
			if (selectedVideoInputDeviceId === 'screen') {
				cameraStream = await navigator.mediaDevices.getDisplayMedia({
					video: {
						cursor: 'always'
					},
					audio: false
				});
			} else {
				cameraStream = await navigator.mediaDevices.getUserMedia({
					video: {
						deviceId: selectedVideoInputDeviceId ? { exact: selectedVideoInputDeviceId } : undefined
					}
				});
			}

			if (cameraStream) {
				await getVideoInputDevices();
				video.srcObject = cameraStream;
				await video.play();
			}
		}
	};

	const stopVideoStream = async () => {
		if (cameraStream) {
			const tracks = cameraStream.getTracks();
			tracks.forEach((track) => track.stop());
		}

		cameraStream = null;
	};

	const takeScreenshot = () => {
		const video = document.getElementById('camera-feed');
		const canvas = document.getElementById('camera-canvas');

		if (!canvas) {
			return;
		}

		const context = canvas.getContext('2d');

		// Make the canvas match the video dimensions
		canvas.width = video.videoWidth;
		canvas.height = video.videoHeight;

		// Draw the image from the video onto the canvas
		context.drawImage(video, 0, 0, video.videoWidth, video.videoHeight);

		// Convert the canvas to a data base64 URL and console log it
		const dataURL = canvas.toDataURL('image/png');
		console.log(dataURL);

		return dataURL;
	};

	const stopCamera = async () => {
		await stopVideoStream();
		camera = false;
	};

	const MIN_DECIBELS = -55;
	const VISUALIZER_BUFFER_LENGTH = 300;
	const SILENCE_TIMEOUT_MS = 900;
	const LISTENING_GRACE_MS = 450;
	const SPEECH_CONFIRMATION_MS = 220;
	const MIN_SPEECH_RMS = 0.018;
	const NOISE_FLOOR_MULTIPLIER = 2.8;

	const transcribeHandler = async (audioBlob, extension = 'webm') => {
		// Create a blob from the audio chunks
		if (!audioBlob || audioBlob.size < 100) {
			console.log('Audio blob too small or empty, skipping transcription');
			return;
		}

		await tick();
		const file = blobToFile(audioBlob, `recording.${extension}`);

		const res = await transcribeAudio(
			localStorage.token,
			file,
			$settings?.audio?.stt?.language
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			console.log(res.text);

				if (res.text !== '') {
					const exiting = isVoiceExitCommand(res.text);
					if (exiting) {
						muted = true;
						hasStartedSpeaking = false;
						confirmed = false;
						audioChunks = [];
						audioPreRollChunks = [];
						assistantSpeaking = true;
						loading = false;

						// Exit is a client control command, not a model prompt. Speak a fixed
						// acknowledgement, wait for it to finish, and then close Voice Mode.
						const exitMessageId = `voice-control-exit-${Date.now()}`;
						audioAbortController?.abort();
						audioAbortController = new AbortController();
						playbackQueues[exitMessageId] = Promise.resolve(true);
						try {
							await enqueueAudio(
								exitMessageId,
								VOICE_EXIT_ACKNOWLEDGEMENT,
								audioAbortController.signal
							);
						} finally {
							delete playbackQueues[exitMessageId];
							assistantSpeaking = false;
							$showCallOverlay = false;
						}
						return;
					}

					const _responses = await submitPrompt(res.text, []);
					console.log(_responses);
			}
		}
	};

	const stopRecordingCallback = async (_continue = true) => {
		if ($showCallOverlay) {
			console.log('%c%s', 'color: red; font-size: 20px;', '🚨 stopRecordingCallback 🚨');

			// deep copy the audioChunks array
			const _audioChunks = audioChunks.slice(0);

			audioChunks = [];
			audioPreRollChunks = [];
			audioContainerHeader = null;
			mediaRecorder = false;

			if (_continue) {
				startRecording();
			}

			if (confirmed) {
				loading = true;
				emoji = null;

				if (cameraStream) {
					const imageUrl = takeScreenshot();

					files = [
						{
							type: 'image',
							url: imageUrl
						}
					];
				}

				const type = _audioChunks[0]?.type || 'audio/webm';
				const extension = type.split('/')[1]?.split(';')[0] || 'webm';
				const audioBlob = new Blob(_audioChunks, { type });
				try {
					await transcribeHandler(audioBlob, extension);
				} finally {
					confirmed = false;
					loading = false;
				}
			}
		} else {
			audioChunks = [];
			mediaRecorder = false;

			if (audioStream) {
				const tracks = audioStream.getTracks();
				tracks.forEach((track) => track.stop());
			}
			audioStream = null;
		}
	};

	const stopAudioAnalysis = () => {
		if (microphoneAnimationFrame !== null) cancelAnimationFrame(microphoneAnimationFrame);
		microphoneAnimationFrame = null;
		if (microphoneAudioContext) void microphoneAudioContext.close();
		microphoneAudioContext = null;
	};

	const startRecording = async () => {
		if ($showCallOverlay) {
			if (!audioStream) {
				audioStream = await navigator.mediaDevices.getUserMedia({
					audio: {
						echoCancellation: true,
						noiseSuppression: true,
						autoGainControl: true
					}
				});
			}

			if (audioStream) {
				// hardware track muting disabled to prevent backend translation errors with malformed WebM files
			}

			mediaRecorder = new MediaRecorder(audioStream);

			mediaRecorder.onstart = () => {
				console.log('Recording started');
				audioChunks = [];
				audioPreRollChunks = [];
				audioContainerHeader = null;
			};

			mediaRecorder.ondataavailable = (event) => {
				if (!event.data.size) return;
				if (!audioContainerHeader) {
					audioContainerHeader = event.data;
					return;
				}
				if (hasStartedSpeaking) {
					audioChunks.push(event.data);
				} else {
					audioPreRollChunks.push(event.data);
					if (audioPreRollChunks.length > 5) audioPreRollChunks.shift();
				}
			};

			mediaRecorder.onstop = (e) => {
				console.log('Recording stopped', audioStream, e);
				stopRecordingCallback();
			};

			stopAudioAnalysis();
			analyseAudio(audioStream);
		}
	};

	const restoreListeningState = async (): Promise<void> => {
		if (exitAfterPlayback) return;
		assistantSpeaking = false;
		loading = false;
		confirmed = false;
		hasStartedSpeaking = false;
		emoji = null;

		if ($showCallOverlay && (!mediaRecorder || mediaRecorder.state === 'inactive')) {
			await startRecording().catch((error) => {
				console.error('Failed to restore voice input:', error);
			});
		}
	};

	const stopAudioStream = async () => {
		stopAudioAnalysis();
		try {
			if (mediaRecorder) {
				mediaRecorder.stop();
			}
		} catch (error) {
			console.log('Error stopping audio stream:', error);
		}

		if (!audioStream) return;

		audioStream.getAudioTracks().forEach(function (track) {
			track.stop();
		});

		audioStream = null;
	};

	// Function to calculate the RMS level from time domain data
	const calculateRMS = (data: Uint8Array) => {
		let sumSquares = 0;
		for (let i = 0; i < data.length; i++) {
			const normalizedValue = (data[i] - 128) / 128; // Normalize the data
			sumSquares += normalizedValue * normalizedValue;
		}
		return Math.sqrt(sumSquares / data.length);
	};

	const analyseAudio = (stream) => {
		microphoneAudioContext = new AudioContext();
		const audioContext = microphoneAudioContext;
		const audioStreamSource = audioContext.createMediaStreamSource(stream);

		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = MIN_DECIBELS;
		audioStreamSource.connect(analyser);

		const timeDomainData = new Uint8Array(analyser.fftSize);
		if (mediaRecorder && mediaRecorder.state === 'inactive') mediaRecorder.start(100);

		const listeningStartedAt = performance.now();
		let lastSpeechTime = listeningStartedAt;
		let candidateStartedAt: number | null = null;
		let noiseFloor = 0.004;
		hasStartedSpeaking = false;

		console.log('🔊 Speech detection started', Math.round(listeningStartedAt));

		const detectSound = () => {
			const processFrame = () => {
				if (!mediaRecorder || !$showCallOverlay) {
					return;
				}

				if (muted || (assistantSpeaking && !($settings?.voiceInterruption ?? false))) {
					// Suppress mic input when muted or when assistant is speaking without interruption enabled
					analyser.maxDecibels = 0;
					analyser.minDecibels = -1;
				} else {
					analyser.minDecibels = MIN_DECIBELS;
					analyser.maxDecibels = -30;
				}

				analyser.getByteTimeDomainData(timeDomainData);
				// Calculate RMS level from time domain data
				rmsLevel = calculateRMS(timeDomainData);

				if (muted || (assistantSpeaking && !($settings?.voiceInterruption ?? false))) {
					rmsLevel = 0;
				}

				const now = performance.now();
				if (now - listeningStartedAt < LISTENING_GRACE_MS) {
					noiseFloor = noiseFloor * 0.9 + rmsLevel * 0.1;
					microphoneAnimationFrame = requestAnimationFrame(processFrame);
					return;
				}

				const speechThreshold = Math.max(MIN_SPEECH_RMS, noiseFloor * NOISE_FLOOR_MULTIPLIER);
				const speechLike = rmsLevel >= speechThreshold;
				const continuingSpeech = rmsLevel >= speechThreshold * 0.55;

				if (!hasStartedSpeaking) {
					if (speechLike) {
						candidateStartedAt ??= now;
						if (now - candidateStartedAt >= SPEECH_CONFIRMATION_MS) {
							hasStartedSpeaking = true;
							audioChunks = audioContainerHeader
								? [audioContainerHeader, ...audioPreRollChunks]
								: [...audioPreRollChunks];
							audioPreRollChunks = [];
							lastSpeechTime = now;
							console.log(
								`🗣️ Speech confirmed (RMS ${rmsLevel.toFixed(3)}, threshold ${speechThreshold.toFixed(3)})`
							);
							stopAllAudio();
						}
					} else {
						candidateStartedAt = null;
						noiseFloor = noiseFloor * 0.98 + rmsLevel * 0.02;
					}
				} else {
					if (continuingSpeech) lastSpeechTime = now;
					if (now - lastSpeechTime > SILENCE_TIMEOUT_MS) {
						confirmed = true;

						if (mediaRecorder) {
							console.log('%c%s', 'color: red; font-size: 20px;', '🔇 Silence detected');
							mediaRecorder.stop();
							return;
						}
					}
				}

				microphoneAnimationFrame = window.requestAnimationFrame(processFrame);
			};

			microphoneAnimationFrame = window.requestAnimationFrame(processFrame);
		};

		detectSound();
	};

	let currentMessageId = null;
	let currentUtterance = null;
	let currentAudio: HTMLAudioElement | null = null;
	let playbackAudioContext: AudioContext | null = null;
	let playbackSource: MediaElementAudioSourceNode | null = null;
	let playbackAnalyser: AnalyserNode | null = null;
	let playbackAnimationFrame: number | null = null;

	const stopPlaybackVisualization = () => {
		if (playbackAnimationFrame !== null) cancelAnimationFrame(playbackAnimationFrame);
		playbackAnimationFrame = null;
		playbackSource?.disconnect();
		playbackSource = null;
		playbackAnalyser = null;
		playbackLevels = Array(5).fill(0.1);
		ttsPlaying = false;
	};

	const startPlaybackVisualization = async (audio: HTMLAudioElement) => {
		stopPlaybackVisualization();
		ttsPlaying = true;

		try {
			playbackAudioContext ??= new AudioContext();
			await playbackAudioContext.resume();
			playbackAnalyser = playbackAudioContext.createAnalyser();
			playbackAnalyser.fftSize = 128;
			playbackAnalyser.smoothingTimeConstant = 0.72;
			playbackSource = playbackAudioContext.createMediaElementSource(audio);
			playbackSource.connect(playbackAnalyser);
			playbackAnalyser.connect(playbackAudioContext.destination);

			const frequencyData = new Uint8Array(playbackAnalyser.frequencyBinCount);
			const updateWaveform = () => {
				if (!ttsPlaying || !playbackAnalyser) return;
				playbackAnalyser.getByteFrequencyData(frequencyData);
				const binsPerBar = Math.max(1, Math.floor(frequencyData.length / playbackLevels.length));
				playbackLevels = playbackLevels.map((_, index) => {
					const start = index * binsPerBar;
					const end = Math.min(frequencyData.length, start + binsPerBar);
					let total = 0;
					for (let i = start; i < end; i++) total += frequencyData[i];
					return Math.max(0.1, total / Math.max(1, end - start) / 255);
				});
				playbackAnimationFrame = requestAnimationFrame(updateWaveform);
			};
			updateWaveform();
		} catch (error) {
			console.warn('Unable to visualize TTS playback:', error);
		}
	};
	const getTTSEngine = () =>
		$config.features?.force_audio_tts_config
			? ($config.features.forced_audio_tts_engine ?? '')
			: $config.features?.enable_kokoro_preload
				? 'browser-kokoro'
				: ($settings.audio?.tts?.engine ?? $config.audio.tts.engine);

	// Get voice: model-specific > user settings > config default
	const getVoiceId = () => {
		if ($config.features?.force_audio_tts_config) {
			return $config.features.forced_audio_tts_voice ?? $config.audio.tts.voice;
		}
		if ($config.features?.enable_kokoro_preload) {
			return $config.features.kokoro_default_voice ?? 'bf_emma';
		}

		let voiceId;
		// Check for model-specific TTS voice first
		if (model?.info?.meta?.tts?.voice) {
			voiceId = model.info.meta.tts.voice;
		} else if ($settings?.audio?.tts?.defaultVoice === $config.audio.tts.voice) {
			voiceId = $settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice;
		} else {
			voiceId = $config?.audio?.tts?.voice;
		}

		return getTTSEngine() === 'browser-kokoro'
			? resolveKokoroVoiceId(voiceId, $config.features?.kokoro_default_voice)
			: voiceId;
	};

	const speakSpeechSynthesisHandler = (content: string, signal: AbortSignal): Promise<boolean> => {
		if ($showCallOverlay) {
			return new Promise<boolean>((resolve) => {
				let voices = [];
				let settled = false;
				let speechTimeout: ReturnType<typeof setTimeout> | null = null;
				let getVoicesLoop: ReturnType<typeof setInterval>;
				const startedAt = Date.now();
				const finish = (result: boolean) => {
					if (settled) return;
					settled = true;
					clearInterval(getVoicesLoop);
					if (speechTimeout) clearTimeout(speechTimeout);
					signal.removeEventListener('abort', handleAbort);
					resolve(result);
				};
				const handleAbort = () => {
					speechSynthesis.cancel();
					finish(false);
				};
				signal.addEventListener('abort', handleAbort, { once: true });
				if (signal.aborted) {
					handleAbort();
					return;
				}
				getVoicesLoop = setInterval(async () => {
					voices = await speechSynthesis.getVoices();
					if (voices.length > 0) {
						clearInterval(getVoicesLoop);
						const voiceId = getVoiceId();
						const voice = voices?.filter((v) => v.voiceURI === voiceId)?.at(0) ?? undefined;

						currentUtterance = new SpeechSynthesisUtterance(content);
						currentUtterance.rate = $settings.audio?.tts?.playbackRate ?? 1;

						if (voice) {
							currentUtterance.voice = voice;
						}

						currentUtterance.onend = async (e) => {
							await new Promise((r) => setTimeout(r, 200));
							finish(true);
						};
						currentUtterance.onerror = (event) => {
							console.error('Browser speech synthesis failed:', event);
							finish(false);
						};
						speechTimeout = setTimeout(
							() => {
								console.error('Browser speech synthesis timed out');
								speechSynthesis.cancel();
								finish(false);
							},
							Math.max(30_000, content.length * 250)
						);
						speechSynthesis.speak(currentUtterance);
					} else if (Date.now() - startedAt >= 5000) {
						console.error('Browser speech synthesis has no available voices');
						finish(false);
					}
				}, 100);
			});
		} else {
			return Promise.resolve(false);
		}
	};

	const playAudio = (audio, signal: AbortSignal): Promise<boolean> => {
		if ($showCallOverlay) {
			return new Promise<boolean>((resolve) => {
				const audioElement = audio instanceof HTMLAudioElement ? audio : null;
				let settled = false;
				const finish = (result: boolean) => {
					if (settled) return;
					settled = true;
					signal.removeEventListener('abort', handleAbort);
					if (currentAudio === audioElement) currentAudio = null;
					stopPlaybackVisualization();
					resolve(result);
				};
				const handleAbort = () => {
					if (audioElement) {
						audioElement.pause();
						audioElement.currentTime = 0;
					}
					finish(false);
				};
				signal.addEventListener('abort', handleAbort, { once: true });
				if (signal.aborted) {
					handleAbort();
					return;
				}

				if (audioElement && audio?.src) {
					currentAudio = audioElement;
					void startPlaybackVisualization(audioElement);
					audioElement.muted = true;
					audioElement.playbackRate = $settings.audio?.tts?.playbackRate ?? 1;

					audioElement
						.play()
						.then(() => {
							audioElement.muted = false;
						})
						.catch((error) => {
							console.error(error);
							finish(false);
						});

					audioElement.onended = async (e) => {
						await new Promise((r) => setTimeout(r, 100));
						finish(true);
					};
					audioElement.onerror = (event) => {
						console.error('Audio playback failed:', event);
						finish(false);
					};
				} else {
					finish(false);
				}
			});
		} else {
			return Promise.resolve(false);
		}
	};

	const stopAllAudio = async () => {
		assistantSpeaking = false;
		interrupted = true;

		if (chatStreaming) {
			stopResponse();
		}
		audioAbortController?.abort();

		if (currentUtterance) {
			speechSynthesis.cancel();
			currentUtterance = null;
		}

		if (currentAudio) {
			currentAudio.muted = true;
			currentAudio.pause();
			currentAudio.currentTime = 0;
			currentAudio = null;
		}
		stopPlaybackVisualization();
	};

	let audioAbortController = new AbortController();

	type SynthesizedAudio = {
		audio: HTMLAudioElement | true | null;
		emoji: Promise<string | null>;
		readyAt: number;
	};
	let ttsSequence = 0;
	const elapsed = (start: number, end = performance.now()) => `${Math.round(end - start)}ms`;

	const fetchAudio = async (
		content: string,
		traceId: string,
		receivedAt: number
	): Promise<SynthesizedAudio> => {
		console.info(`[Voice TTS ${traceId}] synthesis requested (+${elapsed(receivedAt)})`);
		try {
			const emojiPromise =
				($settings?.showEmojiInCall ?? false)
					? generateEmoji(localStorage.token, modelId, content, chatId).catch((error) => {
							console.error('Failed to generate call emoji:', error);
							return null;
						})
					: Promise.resolve(null);

			if (getTTSEngine() === 'browser-kokoro') {
				const kokoroWorker = await getOrInitKokoroWorker(
					$settings.audio?.tts?.engineConfig?.dtype ??
						$config.features?.kokoro_preload_dtype ??
						'q8',
					$config.features?.kokoro_device ?? 'auto',
					getVoiceId()
				);
				const url = await kokoroWorker
					.generate({
						text: content,
						voice: getVoiceId()
					})
					.catch((error) => {
						console.error(error);
						toast.error(`${error}`);
					});

				const readyAt = performance.now();
				console.info(`[Voice TTS ${traceId}] synthesis ready (+${elapsed(receivedAt, readyAt)})`);
				return { audio: url ? new Audio(url) : null, emoji: emojiPromise, readyAt };
			} else if (getTTSEngine() !== '') {
				const res = await synthesizeOpenAISpeech(localStorage.token, getVoiceId(), content).catch(
					(error) => {
						console.error(error);
						return null;
					}
				);

				if (!res) return { audio: null, emoji: emojiPromise, readyAt: performance.now() };
				const blob = await res.blob();
				const readyAt = performance.now();
				console.info(`[Voice TTS ${traceId}] synthesis ready (+${elapsed(receivedAt, readyAt)})`);
				return {
					audio: new Audio(URL.createObjectURL(blob)),
					emoji: emojiPromise,
					readyAt
				};
			} else {
				return { audio: true, emoji: emojiPromise, readyAt: performance.now() };
			}
		} catch (error) {
			console.error('Error synthesizing speech:', error);
			return { audio: null, emoji: Promise.resolve(null), readyAt: performance.now() };
		}
	};

	let playbackQueues: Record<string, Promise<boolean>> = {};

	const enqueueAudio = (id: string, content: string, signal: AbortSignal): Promise<boolean> => {
		const receivedAt = performance.now();
		const traceId = `${id.slice(0, 8)}-${++ttsSequence}`;
		console.info(`[Voice TTS ${traceId}] sentence received (${content.length} characters)`);
		const audioPromise = fetchAudio(content, traceId, receivedAt);
		const previous = playbackQueues[id] ?? Promise.resolve(true);
		playbackQueues[id] = previous.then(async (shouldContinue) => {
			if (!shouldContinue || signal.aborted) return false;

			const item = await audioPromise;
			if (signal.aborted || item.audio === null) return false;
			void item.emoji.then((generatedEmoji) => {
				if (!signal.aborted) emoji = generatedEmoji;
			});

			const playbackStartedAt = performance.now();
			console.info(
				`[Voice TTS ${traceId}] playback started (+${elapsed(receivedAt, playbackStartedAt)}, ready-to-play ${elapsed(item.readyAt, playbackStartedAt)})`
			);

			if (getTTSEngine() === '') {
				const played = await speakSpeechSynthesisHandler(content, signal);
				console.info(
					`[Voice TTS ${traceId}] playback ${played ? 'ended' : 'stopped'} (${elapsed(playbackStartedAt)})`
				);
				return played;
			}

			const played = await playAudio(item.audio, signal);
			console.info(
				`[Voice TTS ${traceId}] playback ${played ? 'ended' : 'stopped'} (${elapsed(playbackStartedAt)})`
			);
			return played;
		});
		return playbackQueues[id];
	};

	const chatStartHandler = async (e) => {
		const { id } = e.detail;

		chatStreaming = true;

		if (currentMessageId !== id) {
			console.log(`Received chat start event for message ID ${id}`);

			currentMessageId = id;
			if (audioAbortController) {
				audioAbortController.abort();
			}
			audioAbortController = new AbortController();

			assistantSpeaking = true;
			playbackQueues[id] = Promise.resolve(true);
		}
	};

	const chatEventHandler = async (e) => {
		const { id, content } = e.detail;
		// "id" here is message id
		// if "id" is not the same as "currentMessageId" then do not process
		// "content" here is a sentence from the assistant,
		// there will be many sentences for the same "id"

		if (currentMessageId === id) {
			console.log(`Received chat event for message ID ${id}: ${content}`);

			try {
				console.log(content);
				enqueueAudio(id, content, audioAbortController.signal);
			} catch (error) {
				console.error('Failed to fetch or play audio:', error);
			}
		}
	};

	const chatFinishHandler = async (e) => {
		const { id } = e.detail;
		chatStreaming = false;
		const completed = await (playbackQueues[id] ?? Promise.resolve(true));
		delete playbackQueues[id];
		if (currentMessageId === id && !audioAbortController.signal.aborted) {
			if (!completed) console.error(`TTS playback failed for message ID ${id}`);
			if (exitAfterPlayback) {
				exitAfterPlayback = false;
				$showCallOverlay = false;
			} else {
				await restoreListeningState();
			}
		}
	};

	const toggleMute = () => {
		muted = !muted;
		if (muted && hasStartedSpeaking) {
			// Abort the ongoing recording so it doesn't accidentally send a partial sentence
			hasStartedSpeaking = false;
			confirmed = false;
			audioChunks = [];
			if (mediaRecorder && mediaRecorder.state === 'recording') {
				mediaRecorder.stop();
			}
		}
	};

	let wasAssistantSpeaking = false;
	$: {
		if (assistantSpeaking && !wasAssistantSpeaking) {
			wasAssistantSpeaking = true;
		} else if (!assistantSpeaking && wasAssistantSpeaking) {
			wasAssistantSpeaking = false;
			// Auto unmute when AI finishes speaking
			if (muted) {
				muted = false;
			}
		}
	}

	const handleKeydown = (e: KeyboardEvent) => {
		// Only handle M key when not typing in an input/textarea
		if (e.key === 'm' || e.key === 'M') {
			const target = e.target as HTMLElement;
			if (
				target.tagName !== 'INPUT' &&
				target.tagName !== 'TEXTAREA' &&
				!target.isContentEditable
			) {
				e.preventDefault();
				toggleMute();
			}
		}
	};

	onMount(async () => {
		const setWakeLock = async () => {
			try {
				wakeLock = await navigator.wakeLock.request('screen');
			} catch (err) {
				// The Wake Lock request has failed - usually system related, such as battery.
				console.log(err);
			}

			if (wakeLock) {
				// Add a listener to release the wake lock when the page is unloaded
				wakeLock.addEventListener('release', () => {
					// the wake lock has been released
					console.log('Wake Lock released');
				});
			}
		};

		if ('wakeLock' in navigator) {
			await setWakeLock();

			document.addEventListener('visibilitychange', async () => {
				// Re-request the wake lock if the document becomes visible
				if (wakeLock !== null && document.visibilityState === 'visible') {
					await setWakeLock();
				}
			});
		}

		model = $models.find((m) => m.id === modelId);

		startRecording();

		eventTarget.addEventListener('chat:start', chatStartHandler);
		eventTarget.addEventListener('chat', chatEventHandler);
		eventTarget.addEventListener('chat:finish', chatFinishHandler);

		document.addEventListener('keydown', handleKeydown);

		return async () => {
			await stopAllAudio();

			stopAudioStream();

			eventTarget.removeEventListener('chat:start', chatStartHandler);
			eventTarget.removeEventListener('chat', chatEventHandler);
			eventTarget.removeEventListener('chat:finish', chatFinishHandler);

			document.removeEventListener('keydown', handleKeydown);

			audioAbortController.abort();
			await tick();

			await stopAllAudio();

			await stopRecordingCallback(false);
			await stopCamera();
		};
	});

	onDestroy(async () => {
		await stopAllAudio();
		await stopRecordingCallback(false);
		await stopCamera();

		await stopAudioStream();
		await playbackAudioContext?.close();
		playbackAudioContext = null;
		eventTarget.removeEventListener('chat:start', chatStartHandler);
		eventTarget.removeEventListener('chat', chatEventHandler);
		eventTarget.removeEventListener('chat:finish', chatFinishHandler);

		document.removeEventListener('keydown', handleKeydown);

		audioAbortController.abort();

		await tick();

		await stopAllAudio();
	});
</script>

{#if $showCallOverlay}
	<div class="max-w-lg w-full h-full max-h-[100dvh] flex flex-col justify-between p-3 md:p-6">
		{#if camera}
			<button
				type="button"
				class="flex justify-center items-center w-full h-20 min-h-20"
				on:click={() => {
					if (assistantSpeaking) {
						stopAllAudio();
					}
				}}
			>
				{#if emoji}
					<div
						class="  transition-all rounded-full"
						style="font-size:{rmsLevel * 100 > 4
							? '4.5'
							: rmsLevel * 100 > 2
								? '4.25'
								: rmsLevel * 100 > 1
									? '3.75'
									: '3.5'}rem;width: 100%; text-align:center;"
					>
						{emoji}
					</div>
				{:else if ttsPlaying}
					<AudioWaveform levels={playbackLevels} compact />
				{:else if loading || assistantSpeaking}
					<svg
						class="size-12 text-gray-900 dark:text-gray-400"
						viewBox="0 0 24 24"
						fill="currentColor"
						xmlns="http://www.w3.org/2000/svg"
						><style>
							.spinner_qM83 {
								animation: spinner_8HQG 1.05s infinite;
							}
							.spinner_oXPr {
								animation-delay: 0.1s;
							}
							.spinner_ZTLf {
								animation-delay: 0.2s;
							}
							@keyframes spinner_8HQG {
								0%,
								57.14% {
									animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
									transform: translate(0);
								}
								28.57% {
									animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
									transform: translateY(-6px);
								}
								100% {
									transform: translate(0);
								}
							}
						</style><circle class="spinner_qM83" cx="4" cy="12" r="3" /><circle
							class="spinner_qM83 spinner_oXPr"
							cx="12"
							cy="12"
							r="3"
						/><circle class="spinner_qM83 spinner_ZTLf" cx="20" cy="12" r="3" /></svg
					>
				{:else}
					<div
						class=" {rmsLevel * 100 > 4
							? ' size-[4.5rem]'
							: rmsLevel * 100 > 2
								? ' size-16'
								: rmsLevel * 100 > 1
									? 'size-14'
									: 'size-12'}  transition-all rounded-full bg-cover bg-center bg-no-repeat"
						style={`background-image: url('${WEBUI_API_BASE_URL}/models/model/profile/image?id=${model?.id}&lang=${$i18n.language}&voice=true');`}
					></div>
				{/if}
				<!-- navbar -->
			</button>
		{/if}

		<div class="flex justify-center items-center flex-1 h-full w-full max-h-full">
			{#if !camera}
				<button
					type="button"
					on:click={() => {
						if (assistantSpeaking) {
							stopAllAudio();
						}
					}}
				>
					{#if emoji}
						<div
							class="  transition-all rounded-full"
							style="font-size:{rmsLevel * 100 > 4
								? '13'
								: rmsLevel * 100 > 2
									? '12'
									: rmsLevel * 100 > 1
										? '11.5'
										: '11'}rem;width:100%;text-align:center;"
						>
							{emoji}
						</div>
					{:else if ttsPlaying}
						<AudioWaveform levels={playbackLevels} />
					{:else if loading || assistantSpeaking}
						<svg
							class="size-44 text-gray-900 dark:text-gray-400"
							viewBox="0 0 24 24"
							fill="currentColor"
							xmlns="http://www.w3.org/2000/svg"
							><style>
								.spinner_qM83 {
									animation: spinner_8HQG 1.05s infinite;
								}
								.spinner_oXPr {
									animation-delay: 0.1s;
								}
								.spinner_ZTLf {
									animation-delay: 0.2s;
								}
								@keyframes spinner_8HQG {
									0%,
									57.14% {
										animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
										transform: translate(0);
									}
									28.57% {
										animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
										transform: translateY(-6px);
									}
									100% {
										transform: translate(0);
									}
								}
							</style><circle class="spinner_qM83" cx="4" cy="12" r="3" /><circle
								class="spinner_qM83 spinner_oXPr"
								cx="12"
								cy="12"
								r="3"
							/><circle class="spinner_qM83 spinner_ZTLf" cx="20" cy="12" r="3" /></svg
						>
					{:else}
						<div
							class=" {rmsLevel * 100 > 4
								? ' size-52'
								: rmsLevel * 100 > 2
									? 'size-48'
									: rmsLevel * 100 > 1
										? 'size-44'
										: 'size-40'} transition-all rounded-full bg-cover bg-center bg-no-repeat"
							style={`background-image: url('${WEBUI_API_BASE_URL}/models/model/profile/image?id=${model?.id}&lang=${$i18n.language}&voice=true');`}
						></div>
					{/if}
				</button>
			{:else}
				<div class="relative flex video-container w-full max-h-full pt-2 pb-4 md:py-6 px-2 h-full">
					<!-- svelte-ignore a11y-media-has-caption -->
					<video
						id="camera-feed"
						autoplay
						class="rounded-2xl h-full min-w-full object-cover object-center"
						playsinline></video>

					<canvas id="camera-canvas" style="display:none;"></canvas>

					<div class=" absolute top-4 md:top-8 left-4">
						<button
							type="button"
							aria-label={$i18n.t('Stop camera')}
							class="p-1.5 text-white cursor-pointer backdrop-blur-xl bg-black/10 rounded-full"
							on:click={() => {
								stopCamera();
							}}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 16 16"
								fill="currentColor"
								class="size-6"
							>
								<path
									d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z"
								/>
							</svg>
						</button>
					</div>
				</div>
			{/if}
		</div>

		<div class="flex flex-col items-center gap-4 pb-4 w-full">
			<button
				type="button"
				class="z-10"
				on:click={() => {
					if (assistantSpeaking) {
						stopAllAudio();
					}
				}}
			>
				<div class="line-clamp-1 text-sm font-normal">
					{#if loading}
						{$i18n.t('Thinking...')}
					{:else if muted}
						{$i18n.t('Muted')}
					{:else if assistantSpeaking}
						{$i18n.t('Tap to interrupt')}
					{:else}
						{$i18n.t('Listening...')}
					{/if}
				</div>
			</button>

			<div class="flex items-center justify-center gap-4 z-10">
				{#if camera}
					<VideoInputMenu
						devices={videoInputDevices}
						on:change={async (e) => {
							console.log(e.detail);
							selectedVideoInputDeviceId = e.detail;
							localStorage.setItem('selectedVideoInputDeviceId', e.detail);
							await stopVideoStream();
							await startVideoStream();
						}}
					>
						<button
							aria-label={$i18n.t('Switch camera')}
							class="p-3 rounded-full bg-gray-50 dark:bg-gray-900"
							type="button"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="size-5"
							>
								<path
									fill-rule="evenodd"
									d="M15.312 11.424a5.5 5.5 0 0 1-9.201 2.466l-.312-.311h2.433a.75.75 0 0 0 0-1.5H3.989a.75.75 0 0 0-.75.75v4.242a.75.75 0 0 0 1.5 0v-2.43l.31.31a7 7 0 0 0 11.712-3.138.75.75 0 0 0-1.449-.39Zm1.23-3.723a.75.75 0 0 0 .219-.53V2.929a.75.75 0 0 0-1.5 0V5.36l-.31-.31A7 7 0 0 0 3.239 8.188a.75.75 0 1 0 1.448.389A5.5 5.5 0 0 1 13.89 6.11l.311.31h-2.432a.75.75 0 0 0 0 1.5h4.243a.75.75 0 0 0 .53-.219Z"
									clip-rule="evenodd"
								/>
							</svg>
						</button>
					</VideoInputMenu>
				{:else}
					<Tooltip content={$i18n.t('Camera')}>
						<button
							aria-label={$i18n.t('Camera')}
							class="p-3 rounded-full bg-gray-50 dark:bg-gray-900"
							type="button"
							on:click={async () => {
								await navigator.mediaDevices.getUserMedia({ video: true });
								startCamera();
							}}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.774 48.774 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"
								/>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z"
								/>
							</svg>
						</button>
					</Tooltip>
				{/if}

				<Tooltip content={muted ? $i18n.t('Unmute') + ' (M)' : $i18n.t('Mute') + ' (M)'}>
					<button
						class="p-3 rounded-full transition-colors duration-200 {muted
							? 'bg-red-500 text-white'
							: 'bg-gray-50 dark:bg-gray-900'}"
						type="button"
						aria-label={muted ? $i18n.t('Unmute') : $i18n.t('Mute')}
						on:click={toggleMute}
					>
						{#if muted}
							<!-- Mic Off icon -->
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z"
								/>
								<line
									x1="3"
									y1="3"
									x2="21"
									y2="21"
									stroke="currentColor"
									stroke-width="1.5"
									stroke-linecap="round"
								/>
							</svg>
						{:else}
							<!-- Mic On icon -->
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z"
								/>
							</svg>
						{/if}
					</button>
				</Tooltip>

				<button
					aria-label={$i18n.t('End call')}
					class="p-3 rounded-full bg-gray-50 dark:bg-gray-900"
					on:click={async () => {
						await stopAudioStream();
						await stopVideoStream();

						console.log(audioStream);
						console.log(cameraStream);

						showCallOverlay.set(false);
						dispatch('close');
					}}
					type="button"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="size-5"
					>
						<path
							d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"
						/>
					</svg>
				</button>
			</div>
		</div>
	</div>
{/if}
