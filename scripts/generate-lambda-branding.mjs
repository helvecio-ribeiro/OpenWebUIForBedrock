import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';

const projectRoot = path.resolve(import.meta.dirname, '..');
const frontendDir = path.join(projectRoot, 'static', 'static');
const backendDir = path.join(projectRoot, 'backend', 'open_webui', 'static');
const sourcePath = path.join(projectRoot, 'static', 'branding', 'lambda-mark.svg');

const source = await readFile(sourcePath, 'utf8');
const darkSource = source.replace('fill="#000"', 'fill="#fff"');
const tileSource = source
	.replace(
		'<path',
		'<rect x="24" y="20" width="464" height="464" rx="96" fill="#fff" stroke="#111" stroke-opacity="0.16" stroke-width="4"/><g transform="translate(46 45) scale(.82)"><path'
	)
	.replace('</svg>', '</g></svg>');

await Promise.all([mkdir(frontendDir, { recursive: true }), mkdir(backendDir, { recursive: true })]);

const render = async (svg, size, output) => {
	await sharp(Buffer.from(svg)).resize(size, size).png().toFile(output);
};

const frontendFiles = {
	'logo.png': [source, 500],
	'splash.png': [source, 500],
	'splash-dark.png': [darkSource, 500],
	'favicon.png': [tileSource, 512],
	'favicon-96x96.png': [tileSource, 96],
	'apple-touch-icon.png': [tileSource, 180],
	'web-app-manifest-192x192.png': [tileSource, 192],
	'web-app-manifest-512x512.png': [tileSource, 512]
};

for (const [filename, [svg, size]] of Object.entries(frontendFiles)) {
	await render(svg, size, path.join(frontendDir, filename));
}

await writeFile(path.join(frontendDir, 'favicon.svg'), tileSource);

// ICO supports PNG-compressed entries. One 32 px entry covers legacy browser use.
const icoPng = await sharp(Buffer.from(tileSource)).resize(32, 32).png().toBuffer();
const icoHeader = Buffer.alloc(22);
icoHeader.writeUInt16LE(0, 0);
icoHeader.writeUInt16LE(1, 2);
icoHeader.writeUInt16LE(1, 4);
icoHeader.writeUInt8(32, 6);
icoHeader.writeUInt8(32, 7);
icoHeader.writeUInt16LE(1, 10);
icoHeader.writeUInt16LE(32, 12);
icoHeader.writeUInt32LE(icoPng.length, 14);
icoHeader.writeUInt32LE(22, 18);
await writeFile(path.join(frontendDir, 'favicon.ico'), Buffer.concat([icoHeader, icoPng]));

await writeFile(
	path.join(projectRoot, 'static', 'favicon.png'),
	await readFile(path.join(frontendDir, 'favicon.png'))
);

for (const filename of [...Object.keys(frontendFiles), 'favicon.svg', 'favicon.ico']) {
	await writeFile(path.join(backendDir, filename), await readFile(path.join(frontendDir, filename)));
}

await writeFile(
	path.join(backendDir, 'site.webmanifest'),
	await readFile(path.join(frontendDir, 'site.webmanifest'))
);

console.log('Generated Lambda WebUI branding assets.');
