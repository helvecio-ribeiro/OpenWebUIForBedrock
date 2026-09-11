<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		createWebPanelSession,
		getWebPanel,
		updateWebPanel,
		type WebPanel
	} from '$lib/apis/webPanels';
	import { activeChatModelIds, config, models, settings, showSidebar } from '$lib/stores';
	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import ArrowPath from '$lib/components/icons/ArrowPath.svelte';
	import ArrowForward from '$lib/components/icons/ArrowForward.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import { copyToClipboard } from '$lib/utils';
	import { normalizeChallengeResult } from '$lib/utils/webPanelActions';

	let panel: WebPanel | null = null;
	let address = '';
	let frameUrl = '';
	let frameKey = 0;
	let loading = true;
	let loadedId = '';
	let mounted = false;
	let frameElement: HTMLIFrameElement | null = null;
	let selectedPassage = '';
	let selectedAction = '';
	let selectionPageTitle = '';
	let selectionPageUrl = '';
	let aiResult = '';
	let exportedContent = '';
	let aiPending = false;
	let aiModelName = '';
	let embeddedLogo = '';
	let panelViewport: HTMLDivElement | null = null;
	let resultPopover: HTMLDivElement | null = null;
	let selectionRect: { left: number; top: number; right: number; bottom: number } | null = null;
	let popoverLeft = 16;
	let popoverTop = 16;
	let popoverPositioned = false;
	const actionLabels: Record<string, string> = {
		explain: 'Explain Text',
		'find-bias': 'Find Bias',
		challenge: 'Challenge Text',
		'summarize-page': 'Summarize Page'
	};

	const formatExportedResult = (result: string, action: string, passage: string) => {
		if (!result) return result;
		const actionTitle = actionLabels[action] ?? 'Analysis';
		if (action === 'summarize-page' || !passage.trim()) return `# ${actionTitle}\n\n${result}`;
		const quote = passage
			.trim()
			.split('\n')
			.map((line) => `> ${line}`)
			.join('\n');
		return `# ${actionTitle}\n\n**Highlighted text:**\n\n${quote}\n\n${result}`;
	};

	$: exportedContent = formatExportedResult(aiResult, selectedAction, selectedPassage);

	const loadEmbeddedLogo = async () => {
		if (embeddedLogo) return embeddedLogo;
		// Use the opaque icon variant: Outlook and some WebKit clipboard targets
		// preserve the image box but incorrectly flatten transparent PNG artwork.
		const blob = await fetch('/static/favicon.png').then((response) => {
			if (!response.ok) throw new Error(`Unable to load Lambda logo (${response.status})`);
			return response.blob();
		});
		embeddedLogo = await new Promise<string>((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => resolve(String(reader.result));
			reader.onerror = () => reject(reader.error);
			reader.readAsDataURL(blob);
		});
		return embeddedLogo;
	};

	const positionResultPopover = async () => {
		popoverPositioned = false;
		await tick();
		if (!panelViewport || !resultPopover || !selectionRect) return;
		const gap = 12;
		const margin = 12;
		const viewportWidth = panelViewport.clientWidth;
		const viewportHeight = panelViewport.clientHeight;
		const width = resultPopover.offsetWidth;
		const height = resultPopover.offsetHeight;
		let left = selectionRect.right + gap;
		if (left + width + margin > viewportWidth) left = selectionRect.left - width - gap;
		if (left < margin)
			left = Math.min(Math.max(margin, selectionRect.left), viewportWidth - width - margin);
		let top = selectionRect.top;
		if (top + height + margin > viewportHeight) top = selectionRect.bottom - height;
		popoverLeft = Math.max(margin, Math.min(left, viewportWidth - width - margin));
		popoverTop = Math.max(margin, Math.min(top, viewportHeight - height - margin));
		popoverPositioned = true;
	};

	const copyResult = async () => {
		if (!aiResult) return;
		const logoDataUrl = await loadEmbeddedLogo().catch((error) => {
			console.warn('Unable to embed the Lambda logo in copied content:', error);
			return '';
		});
		const modelName = document.createElement('span');
		modelName.textContent = aiModelName || 'Unknown';
		const brandingHeader = `<div style="font-family: Arial, Helvetica, sans-serif; color: #111827; margin: 0 0 18px 0;">
			<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse: collapse; margin: 0 0 8px 0;"><tr>
				${logoDataUrl ? `<td style="vertical-align: middle; padding: 0 10px 0 0;"><img src="${logoDataUrl}" width="36" height="36" alt="Lambda WebUI" style="display: block; width: 36px; height: 36px; border: 0;" /></td>` : ''}
				<td style="vertical-align: middle; font-family: Arial, Helvetica, sans-serif; font-size: 24px; line-height: 30px; font-weight: 700; color: #111827;">Lambda WebUI</td>
			</tr></table>
			<div style="font-family: Arial, Helvetica, sans-serif; font-size: 13px; line-height: 20px; color: #6b7280; margin: 0 0 10px 0;"><strong style="font-weight: 700; color: #4b5563;">Model:</strong> ${modelName.innerHTML}</div>
			<hr style="border: 0; border-top: 1px solid #d1d5db; margin: 0;" />
		</div>`;
		if (await copyToClipboard(exportedContent, null, true, brandingHeader))
			toast.success('Copied to clipboard');
		else toast.error('Unable to copy the result');
	};

	const loadFrame = async () => {
		if (!panel?.url) {
			frameUrl = '';
			return;
		}
		const session = await createWebPanelSession(localStorage.token, panel.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (session) frameUrl = session.proxy_url;
	};

	const normalizeUrl = (value: string) => {
		const trimmed = value.trim();
		if (!trimmed) return '';
		return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
	};

	const load = async (id: string) => {
		loadedId = id;
		loading = true;
		panel = await getWebPanel(localStorage.token, id).catch((error) => {
			toast.error(`${error}`);
			goto('/');
			return null;
		});
		if (panel) {
			address = panel.url;
			await loadFrame();
		}
		loading = false;
	};

	$: if (mounted && $page.params.id && $page.params.id !== loadedId) load($page.params.id);

	const navigate = async () => {
		if (!panel) return;
		const url = normalizeUrl(address);
		if (!url) return;
		let hostname = '';
		try {
			hostname = new URL(url).hostname;
		} catch {
			toast.error('Enter a valid web address');
			return;
		}
		const values: { url: string; title?: string } = { url };
		if (panel.title === 'New Panel' || panel.title === 'New Tab') values.title = hostname;
		const updated = await updateWebPanel(localStorage.token, panel.id, values).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (!updated) return;
		panel = updated;
		address = updated.url;
		await loadFrame();
		frameKey += 1;
		window.dispatchEvent(new CustomEvent('web-panels-changed'));
	};

	const navigateHistory = (action: 'back' | 'forward' | 'reload') => {
		if (!frameElement?.contentWindow) return;
		frameElement.contentWindow.postMessage(
			{ source: 'open-webui-web-panel-host', type: 'navigation', action },
			'*'
		);
	};

	const runSelectionAction = async (action: string, text: string, context = '') => {
		selectedAction = action;
		selectedPassage = text;
		aiResult = '';
		aiPending = true;
		void positionResultPopover();
		let sessionModel = '';
		try {
			sessionModel = JSON.parse(sessionStorage.activeChatModelIds || '[]').find(Boolean) ?? '';
		} catch {}
		const modelCandidates = [
			...$activeChatModelIds,
			sessionModel,
			...($settings?.models ?? []),
			...($config?.default_models?.split(',').map((modelId: string) => modelId.trim()) ?? [])
		].filter(Boolean);
		const model =
			modelCandidates.find((modelId) => $models.some((item) => item.id === modelId)) ?? '';
		if (!model) {
			toast.error('Select a model in Chat before using Browser AI actions');
			aiPending = false;
			aiResult = 'No model is selected. Select a model in Chat and try again.';
			void positionResultPopover();
			return;
		}
		const modelItem = $models.find((item) => item.id === model);
		if (!modelItem) {
			toast.error('The selected model is no longer available');
			aiPending = false;
			aiResult = 'The selected model is no longer available. Select another model in Chat.';
			void positionResultPopover();
			return;
		}
		aiModelName = modelItem.name ?? modelItem.id;
		const instruction: Record<string, string> = {
			explain:
				'Explain the selected passage in clear language. Identify its meaning, relevant background, important terms, and why it matters. Use the page title and URL only as contextual metadata. Clearly distinguish page context from background knowledge. Finish with a concise "Further references" section containing useful topics, search terms, and reputable named sources. Include links only when you are confident they are accurate. Do not invent facts, citations, or URLs.',
			'find-bias':
				'Analyze the selected passage for bias and framing. Examine loaded or emotionally persuasive language, unsupported assumptions, selective emphasis, omitted context, source selection, and perspectives that are privileged or excluded. Distinguish demonstrable bias from ordinary editorial focus, uncertainty, or justified emphasis. Do not infer the author’s motives, identity, or political affiliation without evidence. Explain how the wording may shape a reader’s interpretation, identify what additional context would permit a fairer assessment, and finish with a concise neutral rewrite of the passage. Use clear Markdown headings and explicitly state when there is insufficient evidence to identify meaningful bias.',
			challenge:
				'Fact-check the selected text. Classify it as exactly one of: True, False, Mostly true, or Mostly false. Identify the individual factual claims, distinguish facts from opinions or predictions, explain supporting or contradicting information, and identify missing context or misleading framing. State uncertainty and knowledge limitations explicitly; do not claim live verification unless the supplied material establishes it. Name authoritative sources or useful searches without inventing evidence, citations, or URLs. Return only Markdown using exactly this structure and section order:\n\n**Verdict:** [True, False, Mostly true, or Mostly false]\n\n**Explanation:** [clear explanation]\n\n**Individual Factual Claims:**\n1. [claim]\n\n**Supporting Information:**\n1. [supporting or contradicting information]\n\n**How to verify:**\n1. [authoritative source or useful search]',
			'summarize-page':
				'Summarize the supplied page content in exactly three coherent paragraphs. Cover the main subject, the most important supporting details, and the broader meaning or implications. Do not use headings, lists, or more than three paragraphs. Ignore navigation labels, advertisements, cookie notices, and other page chrome when they appear in the supplied text. Do not invent information that is absent from the page.'
		};
		const response = await generateOpenAIChatCompletion(localStorage.token, {
			model,
			model_item: modelItem,
			stream: false,
			messages: [
				{ role: 'system', content: instruction[action] ?? instruction.explain },
				{
					role: 'user',
					content: `Page title: ${selectionPageTitle || panel?.title || ''}\nPage URL: ${selectionPageUrl || panel?.url || ''}\n\n${action === 'summarize-page' ? 'Cleaned article content' : 'Selected passage'}:\n${text}${action === 'challenge' && context ? `\n\nSurrounding context:\n${context}` : ''}`
				}
			]
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		const result = response?.choices?.[0]?.message?.content ?? response?.message?.content ?? '';
		aiResult = action === 'challenge' ? normalizeChallengeResult(result) : result;
		aiPending = false;
		void positionResultPopover();
	};

	const handlePanelMessage = async (event: MessageEvent) => {
		if (!frameElement || event.source !== frameElement.contentWindow) return;
		const data = event.data;
		if (data?.source !== 'open-webui-web-panel') return;
		if (data.type === 'navigated' && typeof data.url === 'string' && panel) {
			address = data.url;
			if (data.url !== panel.url) {
				const updated = await updateWebPanel(localStorage.token, panel.id, { url: data.url }).catch(
					() => null
				);
				if (updated) panel = updated;
			}
		} else if (data.type === 'selection-action' && typeof data.text === 'string') {
			selectionRect = data.rect ?? null;
			selectionPageTitle = typeof data.title === 'string' ? data.title : '';
			selectionPageUrl = typeof data.url === 'string' ? data.url : '';
			await runSelectionAction(
				data.action,
				data.text.slice(0, data.action === 'summarize-page' ? 50000 : 12000),
				typeof data.context === 'string' ? data.context.slice(0, 4000) : ''
			);
		}
	};

	onMount(() => {
		mounted = true;
		void loadEmbeddedLogo().catch((error) =>
			console.warn('Unable to preload the Lambda clipboard logo:', error)
		);
		window.addEventListener('message', handlePanelMessage);
		window.addEventListener('resize', positionResultPopover);
		return () => {
			window.removeEventListener('message', handlePanelMessage);
			window.removeEventListener('resize', positionResultPopover);
		};
	});
</script>

<svelte:head><title>{panel?.title ?? 'Browser'}</title></svelte:head>

<div
	id="chat-pane"
	class="h-screen max-h-[100dvh] min-h-0 w-full max-w-full flex flex-col bg-white dark:bg-gray-950 transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''}"
>
	<div
		class="flex shrink-0 flex-col gap-2 border-b border-gray-100 p-2 dark:border-gray-800 sm:flex-row"
	>
		<div class="flex shrink-0 items-center gap-0.5">
			<button
				class="flex size-9 items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-gray-800"
				type="button"
				disabled={!frameUrl}
				on:click={() => navigateHistory('back')}
				aria-label="Back"><ChevronLeft className="size-5" /></button
			>
			<button
				class="flex size-9 items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-gray-800"
				type="button"
				disabled={!frameUrl}
				on:click={() => navigateHistory('forward')}
				aria-label="Forward"><ChevronRight className="size-5" /></button
			>
			<button
				class="flex size-9 items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-gray-800"
				type="button"
				disabled={!frameUrl}
				on:click={() => navigateHistory('reload')}
				aria-label="Reload"><ArrowPath className="size-4" /></button
			>
		</div>
		<form class="flex min-w-0 flex-1 gap-1" on:submit|preventDefault={navigate}>
			<input
				class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-transparent px-3 py-1.5 text-sm outline-none focus:border-gray-400 dark:border-gray-700"
				bind:value={address}
				placeholder="Enter a web address"
				aria-label="Web address"
			/>
			<button
				class="flex size-9 items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
				type="submit"
				aria-label="Go"><ArrowForward className="size-4" /></button
			>
		</form>
	</div>
	{#if loading}
		<div class="flex flex-1 items-center justify-center text-sm text-gray-500">Loading...</div>
	{:else if !frameUrl}
		<div
			class="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-gray-500"
		>
			<GlobeAlt className="size-10" />
			<div>Enter an address above to open a web page.</div>
		</div>
	{:else}
		<div bind:this={panelViewport} class="relative min-h-0 flex-1">
			{#key frameKey}
				<iframe
					bind:this={frameElement}
					title={panel?.title ?? 'Browser tab'}
					src={frameUrl}
					class="h-full w-full border-0 bg-white"
					sandbox="allow-downloads allow-forms allow-modals allow-popups allow-scripts"
					referrerpolicy="strict-origin-when-cross-origin"
				></iframe>
			{/key}
			{#if selectedPassage}
				<div
					bind:this={resultPopover}
					class="absolute z-20 flex max-h-[calc(100%-1.5rem)] w-[min(28rem,calc(100%-1.5rem))] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-xl transition-opacity dark:border-gray-700 dark:bg-gray-900"
					class:opacity-0={!popoverPositioned}
					style:left={`${popoverLeft}px`}
					style:top={`${popoverTop}px`}
				>
					<div
						class="flex shrink-0 items-center justify-between gap-2 rounded-t-2xl bg-gray-100 px-4 py-3 text-black"
					>
						<div>
							<div class="font-medium">{actionLabels[selectedAction] ?? selectedAction}</div>
							{#if aiModelName}
								<div class="text-xs text-black">Using {aiModelName}</div>
							{/if}
						</div>
						<div class="flex items-center gap-1">
							<button
								type="button"
								class="flex size-8 items-center justify-center rounded-lg text-black hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-40"
								disabled={aiPending || !aiResult}
								on:click={copyResult}
								aria-label="Copy result"
								title="Copy"
							>
								<Clipboard className="size-4" strokeWidth="1.5" />
							</button>
							<button
								class="rounded-lg px-2 py-1 text-black hover:bg-gray-200"
								on:click={() => {
									selectedPassage = '';
									aiResult = '';
								}}>Close</button
							>
						</div>
					</div>
					<div class="min-h-0 flex-1 overflow-y-auto p-4 text-sm leading-6">
						<div class="mb-3 border-l-2 border-gray-300 pl-3 text-xs text-gray-500">
							{selectedAction === 'summarize-page' ? 'Entire page' : selectedPassage}
						</div>
						{#if aiPending}
							Thinking...
						{:else if aiResult}
							<div class="prose markdown-prose-sm min-w-full max-w-full dark:prose-invert">
								<Markdown
									id={`web-panel-${panel?.id ?? 'result'}-${selectedAction}`}
									content={aiResult}
									allowEmbeds={false}
									editCodeBlock={false}
								/>
							</div>
						{:else}
							No response was returned.
						{/if}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>
