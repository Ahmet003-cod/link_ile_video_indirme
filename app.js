document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const mediaForm = document.getElementById('media-form');
  const urlInput = document.getElementById('url-input');
  const pasteBtn = document.getElementById('paste-btn');
  const clearBtn = document.getElementById('clear-btn');
  const loadBtn = document.getElementById('load-btn');
  const loader = document.getElementById('loader');
  const loaderText = document.getElementById('loader-text');
  const alertBox = document.getElementById('alert-box');
  const alertTitle = document.getElementById('alert-title');
  const alertMessage = document.getElementById('alert-message');
  const alertClose = document.getElementById('alert-close');
  const previewSection = document.getElementById('preview-section');
  const mediaContainer = document.getElementById('media-container');
  const mediaTypeBadge = document.getElementById('media-type-badge');
  const mediaTypeText = document.getElementById('media-type-text');
  const mediaDetailsText = document.getElementById('media-details-text');
  const mediaInfoBox = document.getElementById('media-info-box');
  const mediaTitleText = document.getElementById('media-title-text');
  const mediaUploaderText = document.getElementById('media-uploader-text');
  const qualityWrapper = document.getElementById('quality-wrapper');
  const qualitySelect = document.getElementById('quality-select');
  const filenameWrapper = document.getElementById('filename-wrapper');
  const filenameInput = document.getElementById('filename-input');
  const fileExtensionBadge = document.getElementById('file-extension-badge');
  const downloadBtn = document.getElementById('download-btn');
  const downloadBtnText = document.getElementById('download-btn-text');
  const directOpenBtn = document.getElementById('direct-open-btn');
  const copyUrlBtn = document.getElementById('copy-url-btn');
  const sampleChips = document.querySelectorAll('.sample-chip');

  // State
  let currentMedia = {
    mode: 'direct', // 'direct' or 'backend'
    url: '',
    type: '',
    extension: '',
    filename: '',
    qualities: []
  };

  const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif', 'tiff'];
  const VIDEO_EXTS = ['mp4', 'webm', 'ogg', 'mov', 'm4v', 'mkv', 'avi', 'wmv', 'flv'];

  // Input event
  urlInput.addEventListener('input', () => {
    clearBtn.style.display = urlInput.value.trim() ? 'flex' : 'none';
  });

  // Clear button
  clearBtn.addEventListener('click', () => {
    urlInput.value = '';
    clearBtn.style.display = 'none';
    urlInput.focus();
    hidePreview();
    hideAlert();
  });

  // Paste button
  pasteBtn.addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        urlInput.value = text.trim();
        clearBtn.style.display = 'flex';
        processUrl(urlInput.value.trim());
      }
    } catch (err) {
      showAlert('Panoya Erişilemedi', 'Lütfen linki doğrudan kutucuğa yapıştırın.', 'info');
    }
  });

  // Sample Chips
  sampleChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const url = chip.getAttribute('data-url');
      urlInput.value = url;
      clearBtn.style.display = 'flex';
      processUrl(url);
    });
  });

  // Form Submit
  mediaForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const url = urlInput.value.trim();
    if (!url) return;
    processUrl(url);
  });

  // Alert close
  alertClose.addEventListener('click', hideAlert);

  // Download button click (100% Direct & Private)
  downloadBtn.addEventListener('click', async () => {
    if (!currentMedia.url) return;

    if (currentMedia.mode === 'backend') {
      const selectedFormat = qualitySelect.value;
      const downloadUrl = `/api/download?url=${encodeURIComponent(currentMedia.url)}&format=${encodeURIComponent(selectedFormat)}`;
      
      showAlert('İndirme Başlatılıyor', 'Sunucu videoyu işliyor ve cihazınıza indiriyor. Lütfen bekleyin...', 'info');
      
      const originalBtnText = downloadBtnText.textContent;
      downloadBtn.disabled = true;
      downloadBtnText.textContent = 'İndiriliyor...';

      // Trigger direct browser download
      window.location.href = downloadUrl;

      setTimeout(() => {
        downloadBtn.disabled = false;
        downloadBtnText.textContent = originalBtnText;
      }, 4000);
    } else {
      // Direct media blob download
      await downloadDirectMedia(currentMedia.url, getFullFilename());
    }
  });

  // Copy URL button
  copyUrlBtn.addEventListener('click', async () => {
    if (!currentMedia.url) return;
    try {
      await navigator.clipboard.writeText(currentMedia.url);
      const originalText = copyUrlBtn.innerHTML;
      copyUrlBtn.innerHTML = '<i class="fa-solid fa-check"></i> <span>Kopyalandı!</span>';
      setTimeout(() => {
        copyUrlBtn.innerHTML = originalText;
      }, 2000);
    } catch (e) {
      showAlert('Kopyalama Başarısız', 'Tarayıcınız kopyalamaya izin vermedi.', 'danger');
    }
  });

  // Main URL Processor
  async function processUrl(rawUrl) {
    hideAlert();
    hidePreview();

    let url = rawUrl;
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'https://' + url;
      urlInput.value = url;
    }

    if (!isValidUrl(url)) {
      showAlert('Geçersiz Bağlantı', 'Lütfen geçerli bir internet adresi (URL) girin.', 'danger');
      return;
    }

    showLoader('Medya taranıyor ve sunucuda analiz ediliyor...');

    // 1. First check if it's YouTube / Social / Platform link -> Send to Backend
    const isPlatform = isSocialOrComplex(url);
    if (isPlatform) {
      const backendHandled = await analyzeWithBackend(url);
      if (backendHandled) return;
    }

    // 2. Direct Extension checks (JPG, PNG, GIF, WebP, MP4, etc.)
    const parsedExt = extractExtension(url);
    if (IMAGE_EXTS.includes(parsedExt)) {
      try {
        await tryLoadImage(url, parsedExt);
        return;
      } catch (e) {}
    } else if (VIDEO_EXTS.includes(parsedExt)) {
      try {
        await tryLoadVideo(url, parsedExt);
        return;
      } catch (e) {}
    }

    // 3. Try Backend Analyzer for any unknown or streaming URL
    const backendSuccess = await analyzeWithBackend(url);
    if (backendSuccess) return;

    // 4. Fallback: try client-side image / video
    const imageSuccess = await attemptImage(url);
    if (imageSuccess) {
      renderDirectImage(url, imageSuccess.width, imageSuccess.height, parsedExt || 'jpg');
      return;
    }

    const videoSuccess = await attemptVideo(url);
    if (videoSuccess) {
      renderDirectVideo(url, videoSuccess.width, videoSuccess.height, videoSuccess.duration, parsedExt || 'mp4');
      return;
    }

    hideLoader();
    showAlert('Medya Yüklenemedi', 'Bağlantı geçerli bir video veya görsel olarak işlenemedi. Lütfen bağlantıyı kontrol edin.', 'danger');
  }

  function isSocialOrComplex(url) {
    const keywords = ['youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com', 'twitter.com', 'x.com', 'facebook.com', 'vimeo.com', 'dailymotion.com', 'reddit.com', 'shorts'];
    return keywords.some(k => url.toLowerCase().includes(k));
  }

  // Analyze URL with Private Python Backend
  async function analyzeWithBackend(url) {
    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });

      const data = await response.json();
      if (data && data.success) {
        renderBackendMedia(data);
        return true;
      } else if (data && data.error) {
        hideLoader();
        showAlert('Hata', data.error, 'danger');
        return true;
      }
    } catch (e) {
      console.warn('Backend analyze error:', e);
    }
    return false;
  }

  // Render Media loaded from Backend
  function renderBackendMedia(data) {
    hideLoader();
    mediaContainer.innerHTML = '';

    if (data.thumbnail) {
      const img = document.createElement('img');
      img.src = data.thumbnail;
      img.alt = data.title || 'Video Önizlemesi';
      mediaContainer.appendChild(img);
    }

    currentMedia = {
      mode: 'backend',
      url: data.original_url,
      type: 'video',
      filename: sanitizeFilename(data.title || 'video'),
      qualities: data.qualities || []
    };

    mediaTypeBadge.className = 'media-badge';
    mediaTypeBadge.innerHTML = '<i class="fa-brands fa-youtube" style="color: #ef4444;"></i> <span>Video Yayını</span>';
    
    const formattedDuration = formatDuration(data.duration);
    mediaDetailsText.textContent = formattedDuration ? `Süre: ${formattedDuration}` : '';

    mediaInfoBox.style.display = 'block';
    mediaTitleText.textContent = data.title || 'Video';
    mediaUploaderText.textContent = data.uploader ? `Kanal / Yükleyen: ${data.uploader}` : '';

    // Quality dropdown options
    qualitySelect.innerHTML = '';
    data.qualities.forEach(q => {
      const option = document.createElement('option');
      option.value = q.format_id;
      option.textContent = `${q.resolution} ${q.size ? '(' + q.size + ')' : ''}`;
      qualitySelect.appendChild(option);
    });

    qualityWrapper.style.display = 'flex';
    filenameWrapper.style.display = 'none';
    directOpenBtn.href = data.original_url;
    downloadBtnText.textContent = 'Cihaza İndir';

    showPreview();
  }

  // Render Direct Image Preview
  function renderDirectImage(url, width, height, ext) {
    hideLoader();
    mediaContainer.innerHTML = '';

    const img = document.createElement('img');
    img.src = url;
    img.alt = 'Önizleme Görseli';
    mediaContainer.appendChild(img);

    currentMedia = {
      mode: 'direct',
      url: url,
      type: 'image',
      extension: '.' + (ext || 'jpg'),
      filename: extractBaseFilename(url) || 'indirilen_gorsel'
    };

    mediaTypeBadge.className = 'media-badge';
    mediaTypeBadge.innerHTML = '<i class="fa-regular fa-image" style="color: #60a5fa;"></i> <span>Görsel</span>';
    mediaDetailsText.textContent = `${width || 0} × ${height || 0} px • ${(ext || 'Görsel').toUpperCase()}`;

    mediaInfoBox.style.display = 'none';
    qualityWrapper.style.display = 'none';
    filenameWrapper.style.display = 'flex';
    filenameInput.value = currentMedia.filename;
    fileExtensionBadge.textContent = currentMedia.extension;
    directOpenBtn.href = currentMedia.url;
    downloadBtnText.textContent = 'Görseli İndir';

    showPreview();
  }

  // Render Direct Video Preview
  function renderDirectVideo(url, width, height, duration, ext) {
    hideLoader();
    mediaContainer.innerHTML = '';

    const video = document.createElement('video');
    video.src = url;
    video.controls = true;
    video.autoplay = false;
    video.playsInline = true;
    mediaContainer.appendChild(video);

    currentMedia = {
      mode: 'direct',
      url: url,
      type: 'video',
      extension: '.' + (ext || 'mp4'),
      filename: extractBaseFilename(url) || 'indirilen_video'
    };

    const formattedDuration = formatDuration(duration);
    mediaTypeBadge.className = 'media-badge';
    mediaTypeBadge.innerHTML = '<i class="fa-solid fa-video" style="color: #a78bfa;"></i> <span>Video</span>';
    mediaDetailsText.textContent = `${width || 0} × ${height || 0} px ${formattedDuration ? '• ' + formattedDuration : ''} • ${(ext || 'Video').toUpperCase()}`;

    mediaInfoBox.style.display = 'none';
    qualityWrapper.style.display = 'none';
    filenameWrapper.style.display = 'flex';
    filenameInput.value = currentMedia.filename;
    fileExtensionBadge.textContent = currentMedia.extension;
    directOpenBtn.href = currentMedia.url;
    downloadBtnText.textContent = 'Videoyu İndir';

    showPreview();
  }

  function getFullFilename() {
    let name = filenameInput.value.trim() || currentMedia.filename || 'medya';
    if (name.endsWith(currentMedia.extension)) {
      name = name.substring(0, name.length - currentMedia.extension.length);
    }
    return `${name}${currentMedia.extension}`;
  }

  // Direct Blob Download
  async function downloadDirectMedia(url, filename) {
    const originalBtnContent = downloadBtn.innerHTML;
    downloadBtn.disabled = true;
    downloadBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> <span>İndiriliyor...</span>';

    try {
      const response = await fetch(url, { mode: 'cors' });
      if (!response.ok) throw new Error('Ağ yanıtı başarısız oldu.');

      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      triggerDownload(blobUrl, filename);

      setTimeout(() => {
        window.URL.revokeObjectURL(blobUrl);
      }, 3000);

      showAlert('İndirme Başlatıldı', `"${filename}" başarıyla indiriliyor.`, 'success');
    } catch (err) {
      triggerDownload(url, filename);
      showAlert('İndirme Başlatıldı', 'Dosya tarayıcı indirme yöneticisine aktarıldı.', 'info');
    } finally {
      downloadBtn.disabled = false;
      downloadBtn.innerHTML = originalBtnContent;
    }
  }

  function triggerDownload(href, filename) {
    const a = document.createElement('a');
    a.href = href;
    a.download = filename || 'medya_dosyasi';
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // Helpers
  function attemptImage(url) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
      img.onerror = () => resolve(false);
      img.src = url;
    });
  }

  function attemptVideo(url) {
    return new Promise((resolve) => {
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.onloadedmetadata = () => resolve({
        width: video.videoWidth,
        height: video.videoHeight,
        duration: video.duration
      });
      video.onerror = () => resolve(false);
      video.src = url;
    });
  }

  function tryLoadImage(url, ext) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        renderDirectImage(url, img.naturalWidth, img.naturalHeight, ext);
        resolve();
      };
      img.onerror = () => reject();
      img.src = url;
    });
  }

  function tryLoadVideo(url, ext) {
    return new Promise((resolve, reject) => {
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.onloadedmetadata = () => {
        renderDirectVideo(url, video.videoWidth, video.videoHeight, video.duration, ext);
        resolve();
      };
      video.onerror = () => reject();
      video.src = url;
    });
  }

  function extractExtension(url) {
    try {
      const urlObj = new URL(url);
      const pathname = urlObj.pathname;
      const lastSegment = pathname.split('/').pop() || '';
      const dotIndex = lastSegment.lastIndexOf('.');
      if (dotIndex !== -1 && dotIndex < lastSegment.length - 1) {
        return lastSegment.substring(dotIndex + 1).toLowerCase().split('?')[0].split('#')[0];
      }
    } catch (e) {
      const match = url.match(/\.([a-zA-Z0-9]+)(?:[?#]|$)/);
      if (match) return match[1].toLowerCase();
    }
    return '';
  }

  function extractBaseFilename(url) {
    try {
      const urlObj = new URL(url);
      const pathname = urlObj.pathname;
      let lastSegment = pathname.split('/').filter(Boolean).pop() || 'medya';
      lastSegment = decodeURIComponent(lastSegment);
      const dotIndex = lastSegment.lastIndexOf('.');
      if (dotIndex !== -1) {
        return sanitizeFilename(lastSegment.substring(0, dotIndex));
      }
      return sanitizeFilename(lastSegment);
    } catch (e) {
      return 'medya_dosyasi';
    }
  }

  function sanitizeFilename(str) {
    return (str || 'medya').replace(/[^a-zA-Z0-9_\-\s]/g, '_').substring(0, 60).trim();
  }

  function formatDuration(seconds) {
    if (!seconds || isNaN(seconds) || seconds === Infinity) return '';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs < 10 ? '0' : ''}${secs}`;
  }

  function isValidUrl(string) {
    try {
      const url = new URL(string);
      return url.protocol === 'http:' || url.protocol === 'https:';
    } catch (_) {
      return false;
    }
  }

  function showLoader(msg = 'Yükleniyor...') {
    loaderText.textContent = msg;
    loader.style.display = 'flex';
  }

  function hideLoader() {
    loader.style.display = 'none';
  }

  function showPreview() {
    previewSection.style.display = 'flex';
    previewSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function hidePreview() {
    previewSection.style.display = 'none';
    mediaContainer.innerHTML = '';
  }

  function showAlert(title, message, type = 'danger') {
    alertTitle.textContent = title;
    alertMessage.textContent = message;
    alertBox.className = `alert-box alert-${type}`;

    const icon = alertBox.querySelector('.alert-icon');
    if (type === 'success') {
      icon.className = 'alert-icon fa-solid fa-circle-check';
    } else if (type === 'info') {
      icon.className = 'alert-icon fa-solid fa-circle-info';
    } else {
      icon.className = 'alert-icon fa-solid fa-circle-exclamation';
    }

    alertBox.style.display = 'flex';
  }

  function hideAlert() {
    alertBox.style.display = 'none';
  }
});
