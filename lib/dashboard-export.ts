const copyComputedStyles = (source: Element, target: Element) => {
  const computed = window.getComputedStyle(source);

  for (const propertyName of Array.from(computed)) {
    const propertyValue = computed.getPropertyValue(propertyName);
    const propertyPriority = computed.getPropertyPriority(propertyName);
    (target as HTMLElement).style.setProperty(propertyName, propertyValue, propertyPriority);
  }
};

const syncCloneTree = (source: Element, target: Element) => {
  copyComputedStyles(source, target);

  if (source instanceof HTMLCanvasElement) {
    const image = document.createElement('img');
    image.src = source.toDataURL('image/png');
    image.width = source.width;
    image.height = source.height;
    image.style.width = `${source.clientWidth || source.width}px`;
    image.style.height = `${source.clientHeight || source.height}px`;
    copyComputedStyles(source, image);
    target.replaceWith(image);
    return;
  }

  if (source instanceof HTMLImageElement && target instanceof HTMLImageElement) {
    target.src = source.currentSrc || source.src;
  }

  if (source instanceof HTMLInputElement && target instanceof HTMLInputElement) {
    target.value = source.value;
  }

  if (source instanceof HTMLTextAreaElement && target instanceof HTMLTextAreaElement) {
    target.value = source.value;
  }

  const sourceChildren = Array.from(source.children);
  const targetChildren = Array.from(target.children);

  sourceChildren.forEach((child, index) => {
    const clonedChild = targetChildren[index];
    if (!clonedChild) return;
    syncCloneTree(child, clonedChild);
  });
};

const buildExportClone = (node: HTMLElement) => {
  const clone = node.cloneNode(true) as HTMLElement;
  syncCloneTree(node, clone);
  return clone;
};

const renderNodeToCanvas = async (
  node: HTMLElement,
  {
    pixelRatio = 2,
    backgroundColor = '#ffffff',
  }: {
    pixelRatio?: number;
    backgroundColor?: string;
  } = {},
) => {
  const clonedNode = buildExportClone(node);
  const width = Math.ceil(node.scrollWidth || node.clientWidth || node.getBoundingClientRect().width);
  const height = Math.ceil(node.scrollHeight || node.clientHeight || node.getBoundingClientRect().height);

  clonedNode.style.margin = '0';
  clonedNode.style.width = `${width}px`;
  clonedNode.style.height = `${height}px`;
  clonedNode.style.boxSizing = 'border-box';
  clonedNode.style.background = backgroundColor;

  const wrapper = document.createElement('div');
  wrapper.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml');
  wrapper.style.width = `${width}px`;
  wrapper.style.height = `${height}px`;
  wrapper.style.background = backgroundColor;
  wrapper.style.overflow = 'hidden';
  wrapper.appendChild(clonedNode);

  const serialized = new XMLSerializer().serializeToString(wrapper);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">
      <foreignObject x="0" y="0" width="100%" height="100%">${serialized}</foreignObject>
    </svg>
  `;

  const dataUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;

  const image = await new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('No se pudo rasterizar el lienzo para exportación.'));
    img.src = dataUrl;
  });

  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.floor(width * pixelRatio));
  canvas.height = Math.max(1, Math.floor(height * pixelRatio));
  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('No se pudo inicializar el canvas de exportación.');
  }

  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  context.fillStyle = backgroundColor;
  context.fillRect(0, 0, width, height);
  context.drawImage(image, 0, 0, width, height);
  return canvas;
};

const triggerDownload = (dataUrl: string, fileName: string) => {
  const anchor = document.createElement('a');
  anchor.href = dataUrl;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
};

const triggerBlobDownload = (blob: Blob, fileName: string) => {
  const objectUrl = URL.createObjectURL(blob);
  triggerDownload(objectUrl, fileName);
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
};

const dataUrlToBytes = (dataUrl: string) => {
  const [, base64Payload = ''] = dataUrl.split(',', 2);
  const binary = window.atob(base64Payload);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
};

const concatPdfChunks = (chunks: Uint8Array[]) => {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Uint8Array(totalLength);
  let offset = 0;

  chunks.forEach((chunk) => {
    merged.set(chunk, offset);
    offset += chunk.length;
  });

  return merged;
};

const buildPdfFromCanvas = (canvas: HTMLCanvasElement, _title: string) => {
  const encoder = new TextEncoder();
  const pageWidthPt = 841.89;
  const pageHeightPt = 595.28;
  const marginPt = 24;
  const contentWidthPt = pageWidthPt - marginPt * 2;
  const contentHeightPt = pageHeightPt - marginPt * 2;
  const pixelsPerPoint = canvas.width / contentWidthPt;
  const sliceHeightPx = Math.max(1, Math.floor(contentHeightPt * pixelsPerPoint));
  const pageSlices: Array<{
    bytes: Uint8Array;
    widthPx: number;
    heightPx: number;
    drawWidthPt: number;
    drawHeightPt: number;
  }> = [];

  for (let startY = 0; startY < canvas.height; startY += sliceHeightPx) {
    const currentSliceHeight = Math.min(sliceHeightPx, canvas.height - startY);
    const sliceCanvas = document.createElement('canvas');
    sliceCanvas.width = canvas.width;
    sliceCanvas.height = currentSliceHeight;
    const sliceContext = sliceCanvas.getContext('2d');
    if (!sliceContext) {
      throw new Error('No se pudo construir la página PDF de exportación.');
    }

    sliceContext.fillStyle = '#ffffff';
    sliceContext.fillRect(0, 0, sliceCanvas.width, sliceCanvas.height);
    sliceContext.drawImage(
      canvas,
      0,
      startY,
      canvas.width,
      currentSliceHeight,
      0,
      0,
      canvas.width,
      currentSliceHeight,
    );

    const jpegDataUrl = sliceCanvas.toDataURL('image/jpeg', 0.94);
    pageSlices.push({
      bytes: dataUrlToBytes(jpegDataUrl),
      widthPx: sliceCanvas.width,
      heightPx: sliceCanvas.height,
      drawWidthPt: contentWidthPt,
      drawHeightPt: (sliceCanvas.height / sliceCanvas.width) * contentWidthPt,
    });
  }

  const objects: Array<{ id: number; body: Uint8Array }> = [];
  const pageObjectIds: number[] = [];
  let nextObjectId = 3;

  pageSlices.forEach((slice, pageIndex) => {
    const pageObjectId = nextObjectId;
    const contentObjectId = nextObjectId + 1;
    const imageObjectId = nextObjectId + 2;
    const imageName = `Im${pageIndex + 1}`;
    const drawHeightPt = Math.min(contentHeightPt, slice.drawHeightPt);
    const translateY = pageHeightPt - marginPt - drawHeightPt;
    const contentStream = `q
${slice.drawWidthPt.toFixed(2)} 0 0 ${drawHeightPt.toFixed(2)} ${marginPt.toFixed(2)} ${translateY.toFixed(2)} cm
/${imageName} Do
Q`;

    objects.push({
      id: imageObjectId,
      body: concatPdfChunks([
        encoder.encode(
          `<< /Type /XObject /Subtype /Image /Width ${slice.widthPx} /Height ${slice.heightPx} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${slice.bytes.length} >>\nstream\n`,
        ),
        slice.bytes,
        encoder.encode('\nendstream'),
      ]),
    });

    const contentBytes = encoder.encode(contentStream);
    objects.push({
      id: contentObjectId,
      body: concatPdfChunks([
        encoder.encode(`<< /Length ${contentBytes.length} >>\nstream\n`),
        contentBytes,
        encoder.encode('\nendstream'),
      ]),
    });

    objects.push({
      id: pageObjectId,
      body: encoder.encode(
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidthPt.toFixed(2)} ${pageHeightPt.toFixed(2)}] /Resources << /XObject << /${imageName} ${imageObjectId} 0 R >> >> /Contents ${contentObjectId} 0 R >>`,
      ),
    });

    pageObjectIds.push(pageObjectId);
    nextObjectId += 3;
  });

  objects.unshift({
    id: 2,
    body: encoder.encode(`<< /Type /Pages /Kids [${pageObjectIds.map((id) => `${id} 0 R`).join(' ')}] /Count ${pageObjectIds.length} >>`),
  });
  objects.unshift({
    id: 1,
    body: encoder.encode(`<< /Type /Catalog /Pages 2 0 R /ViewerPreferences << /DisplayDocTitle true >> >>`),
  });

  const header = encoder.encode('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');
  const chunks: Uint8Array[] = [header];
  const offsets: number[] = [0];
  let currentOffset = header.length;

  objects
    .sort((left, right) => left.id - right.id)
    .forEach((object) => {
      offsets[object.id] = currentOffset;
      const prefix = encoder.encode(`${object.id} 0 obj\n`);
      const suffix = encoder.encode('\nendobj\n');
      chunks.push(prefix, object.body, suffix);
      currentOffset += prefix.length + object.body.length + suffix.length;
    });

  const xrefOffset = currentOffset;
  const maxObjectId = objects[objects.length - 1]?.id || 0;
  const xrefLines = ['xref', `0 ${maxObjectId + 1}`, '0000000000 65535 f '];

  for (let objectId = 1; objectId <= maxObjectId; objectId += 1) {
    xrefLines.push(`${String(offsets[objectId] || 0).padStart(10, '0')} 00000 n `);
  }

  const trailer = `trailer
<< /Size ${maxObjectId + 1} /Root 1 0 R >>
startxref
${xrefOffset}
%%EOF`;

  chunks.push(encoder.encode(`${xrefLines.join('\n')}\n${trailer}`));
  return new Blob([concatPdfChunks(chunks)], { type: 'application/pdf' });
};

export const exportDashboardAsImage = async (
  node: HTMLElement,
  {
    fileName,
    format,
  }: {
    fileName: string;
    format: 'png' | 'jpg';
  },
) => {
  const dataUrl = await buildDashboardImageDataUrl(node, format);
  triggerDownload(dataUrl, `${fileName}.${format}`);
  return dataUrl;
};

const buildDashboardImageDataUrl = async (
  node: HTMLElement,
  format: 'png' | 'jpg',
) => {
  const canvas = await renderNodeToCanvas(node, {
    pixelRatio: window.devicePixelRatio > 1 ? 2 : 1.5,
    backgroundColor: '#ffffff',
  });

  const mimeType = format === 'png' ? 'image/png' : 'image/jpeg';
  return canvas.toDataURL(mimeType, format === 'png' ? undefined : 0.96);
};

export const exportDashboardAsPdf = async (
  node: HTMLElement,
  {
    fileName,
  }: {
    fileName: string;
  },
) => {
  const canvas = await renderNodeToCanvas(node, {
    pixelRatio: window.devicePixelRatio > 1 ? 2 : 1.5,
    backgroundColor: '#ffffff',
  });
  const pdfBlob = buildPdfFromCanvas(canvas, fileName);
  triggerBlobDownload(pdfBlob, `${fileName}.pdf`);
};
