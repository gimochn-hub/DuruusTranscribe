// Initialize Mermaid with dark theme
mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    themeVariables: {
        darkMode: true,
        background: '#090e17',
        primaryColor: '#059669',
        primaryTextColor: '#f8fafc',
        primaryBorderColor: '#10b981',
        lineColor: '#64748b',
        secondaryColor: '#1e293b',
        tertiaryColor: '#0f172a',
        fontFamily: 'Plus Jakarta Sans, sans-serif',
    },
    mindmap: {
        padding: 16,
    }
});

document.addEventListener('DOMContentLoaded', () => {
    // ─── DOM Elements ───
    const tabYtBtn = document.getElementById('tab-yt-btn');
    const tabFileBtn = document.getElementById('tab-file-btn');
    const tabYoutube = document.getElementById('tab-youtube');
    const tabFile = document.getElementById('tab-file');
    const btnPasteUrl = document.getElementById('btn-paste-url');
    const youtubeUrlInput = document.getElementById('youtube-url');

    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const selectedFileName = document.getElementById('selected-file-name');

    const groqKeyInput = document.getElementById('input-groq-key');
    const geminiKeyInput = document.getElementById('input-gemini-key');
    const groqSavedBadge = document.getElementById('groq-saved-badge');
    const geminiSavedBadge = document.getElementById('gemini-saved-badge');
    const btnToggleGroq = document.getElementById('btn-toggle-groq');
    const btnToggleGemini = document.getElementById('btn-toggle-gemini');
    const btnClearKeys = document.getElementById('btn-clear-keys');

    const toggleMetadata = document.getElementById('toggle-metadata');
    const metadataBody = document.getElementById('metadata-body');
    const toggleKeys = document.getElementById('toggle-keys');
    const keysBody = document.getElementById('keys-body');
    const keyStatusIndicator = document.getElementById('key-status-indicator');

    const transcribeForm = document.getElementById('transcribe-form');
    const btnSubmit = document.getElementById('btn-submit');
    const formCard = document.getElementById('form-card');
    const processModeInput = document.getElementById('process-mode-input');
    const modeCards = document.querySelectorAll('.mode-card');

    const progressCard = document.getElementById('progress-card');
    const progressTitleText = document.getElementById('progress-title-text');
    const progressStatusDetail = document.getElementById('progress-status-detail');
    const progressPercentLabel = document.getElementById('progress-percent-label');
    const progressFillBar = document.getElementById('progress-fill-bar');
    const step4Label = document.getElementById('step-4-label');

    const resultCard = document.getElementById('result-card');
    const resultDocTitle = document.getElementById('result-doc-title');
    const btnBundleDownload = document.getElementById('btn-bundle-download');
    const btnDownload = document.getElementById('btn-download');
    const btnSummaryDownload = document.getElementById('btn-summary-download');
    const btnRestart = document.getElementById('btn-restart');

    const summaryResultCard = document.getElementById('summary-result-card');
    const summaryDocTitle = document.getElementById('summary-doc-title');
    const summaryModeLabel = document.getElementById('summary-mode-label');
    const btnCopySummary = document.getElementById('btn-copy-summary');
    const btnBundleDlSummary = document.getElementById('btn-bundle-dl-summary');
    const btnSummaryDlMain = document.getElementById('btn-summary-dl-main');
    const btnTranscriptFromSummary = document.getElementById('btn-transcript-from-summary');
    const btnRestartSummary = document.getElementById('btn-restart-summary');

    const errorCard = document.getElementById('error-card');
    const errorMessageText = document.getElementById('error-message-text');
    const btnErrorRetry = document.getElementById('btn-error-retry');

    let currentSourceType = 'youtube';
    let currentMode = 'transcript';
    let eventSource = null;
    let currentSummaryData = null;

    // ─── Health Check ───
    checkServerHealth();

    async function checkServerHealth() {
        try {
            const res = await fetch('/api/health');
            if (res.ok) {
                const data = await res.json();
                if (data.has_groq_key && data.has_gemini_key) {
                    keyStatusIndicator.textContent = 'Siap (.env aktif)';
                    keyStatusIndicator.classList.add('ready');
                } else {
                    keyStatusIndicator.textContent = 'Perlu API Key';
                }
            }
        } catch (e) {
            console.warn('Health check error:', e);
        }
    }

    // ─── Source Tabs ───
    tabYtBtn.addEventListener('click', () => {
        currentSourceType = 'youtube';
        tabYtBtn.classList.add('active');
        tabFileBtn.classList.remove('active');
        tabYoutube.classList.add('active');
        tabFile.classList.remove('active');
    });

    tabFileBtn.addEventListener('click', () => {
        currentSourceType = 'file';
        tabFileBtn.classList.add('active');
        tabYtBtn.classList.remove('active');
        tabFile.classList.add('active');
        tabYoutube.classList.remove('active');
    });

    // ─── Paste YouTube URL ───
    btnPasteUrl.addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (text) youtubeUrlInput.value = text.trim();
        } catch (err) {
            youtubeUrlInput.focus();
        }
    });

    // ─── Mode Cards ───
    modeCards.forEach(card => {
        card.addEventListener('click', () => {
            modeCards.forEach(c => c.classList.remove('active'));
            card.classList.add('active');
            currentMode = card.dataset.mode;
            processModeInput.value = currentMode;

            // Update stepper label for summary mode
            if (currentMode === 'summary') {
                if (step4Label) step4Label.textContent = 'Ringkasan & Refleksi';
            } else if (currentMode === 'both') {
                if (step4Label) step4Label.textContent = 'Dalil + Ringkasan';
            } else {
                if (step4Label) step4Label.textContent = 'Preservasi Dalil';
            }
        });
    });

    // ─── File Drag & Drop ───
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelect();
        }
    });
    fileInput.addEventListener('change', handleFileSelect);

    function handleFileSelect() {
        if (fileInput.files && fileInput.files[0]) {
            const file = fileInput.files[0];
            const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
            selectedFileName.textContent = `📁 ${file.name} (${sizeMB} MB)`;
            selectedFileName.classList.remove('hidden');
        } else {
            selectedFileName.classList.add('hidden');
        }
    }

    // ─── Accordions ───
    toggleMetadata.addEventListener('click', () => {
        metadataBody.classList.toggle('hidden');
        toggleMetadata.querySelector('.acc-arrow').textContent =
            metadataBody.classList.contains('hidden') ? '▾' : '▴';
    });
    toggleKeys.addEventListener('click', () => {
        keysBody.classList.toggle('hidden');
        toggleKeys.querySelector('.acc-arrow').textContent =
            keysBody.classList.contains('hidden') ? '▾' : '▴';
    });

    // ─── API Key localStorage Persistence ───
    const LS_GROQ = 'kajian_groq_key';
    const LS_GEMINI = 'kajian_gemini_key';

    function saveKey(storageKey, value) {
        if (value && value.trim()) {
            // Simpan dengan btoa (basic obfuscation — bukan enkripsi kuat)
            try { localStorage.setItem(storageKey, btoa(unescape(encodeURIComponent(value.trim())))); }
            catch(e) { localStorage.setItem(storageKey, value.trim()); }
        } else {
            localStorage.removeItem(storageKey);
        }
    }

    function loadKey(storageKey) {
        const raw = localStorage.getItem(storageKey);
        if (!raw) return '';
        try { return decodeURIComponent(escape(atob(raw))); }
        catch(e) { return raw; }
    }

    function updateBadge(input, badge) {
        const val = loadKey(input === groqKeyInput ? LS_GROQ : LS_GEMINI);
        if (val) {
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }

    function loadSavedKeys() {
        const groqVal = loadKey(LS_GROQ);
        const geminiVal = loadKey(LS_GEMINI);

        if (groqVal) {
            groqKeyInput.value = groqVal;
            groqSavedBadge.classList.remove('hidden');
        }
        if (geminiVal) {
            geminiKeyInput.value = geminiVal;
            geminiSavedBadge.classList.remove('hidden');
        }

        // Jika ada key tersimpan, otomatis buka accordion keys agar user tahu
        if (groqVal || geminiVal) {
            keysBody.classList.remove('hidden');
            toggleKeys.querySelector('.acc-arrow').textContent = '▴';
        }
    }

    // Auto-save saat input berubah (debounce 800ms)
    let groqSaveTimer, geminiSaveTimer;
    groqKeyInput.addEventListener('input', () => {
        clearTimeout(groqSaveTimer);
        groqSaveTimer = setTimeout(() => {
            saveKey(LS_GROQ, groqKeyInput.value);
            updateBadge(groqKeyInput, groqSavedBadge);
        }, 800);
    });
    geminiKeyInput.addEventListener('input', () => {
        clearTimeout(geminiSaveTimer);
        geminiSaveTimer = setTimeout(() => {
            saveKey(LS_GEMINI, geminiKeyInput.value);
            updateBadge(geminiKeyInput, geminiSavedBadge);
        }, 800);
    });

    // Show/hide password toggle
    function setupToggle(btn, input) {
        btn.addEventListener('click', () => {
            const isHidden = input.type === 'password';
            input.type = isHidden ? 'text' : 'password';
            btn.textContent = isHidden ? '🙈' : '👁';
        });
    }
    setupToggle(btnToggleGroq, groqKeyInput);
    setupToggle(btnToggleGemini, geminiKeyInput);

    // Hapus semua key tersimpan
    btnClearKeys.addEventListener('click', () => {
        if (!confirm('Hapus semua API Key yang tersimpan di browser ini?')) return;
        localStorage.removeItem(LS_GROQ);
        localStorage.removeItem(LS_GEMINI);
        groqKeyInput.value = '';
        geminiKeyInput.value = '';
        groqSavedBadge.classList.add('hidden');
        geminiSavedBadge.classList.add('hidden');
        // Tampilkan feedback singkat
        const orig = btnClearKeys.innerHTML;
        btnClearKeys.textContent = '✓ Terhapus!';
        setTimeout(() => { btnClearKeys.innerHTML = orig; }, 2000);
    });

    // Load keys saat halaman pertama dibuka
    loadSavedKeys();

    // ─── Summary Tabs ───
    const summaryTabBtns = document.querySelectorAll('.summary-tab-btn');
    summaryTabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            summaryTabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const panelId = `spanel-${btn.dataset.stab}`;
            document.querySelectorAll('.summary-tab-panel').forEach(p => p.classList.remove('active'));
            const panel = document.getElementById(panelId);
            if (panel) panel.classList.add('active');

            // Render Mermaid diagram when its tab is opened
            if (btn.dataset.stab === 'diagram' && currentSummaryData) {
                renderMermaid(currentSummaryData.bagan_mermaid);
            }
        });
    });

    // ─── Form Submit ───
    transcribeForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        if (currentSourceType === 'youtube' && !youtubeUrlInput.value.trim()) {
            alert('Silakan masukkan tautan video YouTube kajian terlebih dahulu.');
            youtubeUrlInput.focus();
            return;
        }
        if (currentSourceType === 'file' && (!fileInput.files || !fileInput.files[0])) {
            alert('Silakan pilih file rekaman kajian untuk diunggah.');
            return;
        }

        const formData = new FormData(transcribeForm);
        formData.set('source_type', currentSourceType);
        formData.set('process_mode', currentMode);

        // Hide all, show progress
        [formCard, errorCard, resultCard, summaryResultCard].forEach(c => c.classList.add('hidden'));
        progressCard.classList.remove('hidden');
        resetStepper();
        updateProgress(0, 'init', 'Menghubungkan ke server...');

        try {
            const res = await fetch('/api/start-job', { method: 'POST', body: formData });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Gagal memulai proses.');
            listenToJobEvents(data.job_id);
        } catch (err) {
            showError(err.message || 'Terjadi kesalahan saat memulai job.');
        }
    });

    // ─── SSE Progress Stream ───
    function listenToJobEvents(jobId) {
        if (eventSource) eventSource.close();
        eventSource = new EventSource(`/api/events/${jobId}`);

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.status === 'processing' || data.status === 'queued') {
                    updateProgress(data.percent || 0, data.stage || 'process', data.message || '');
                } else if (data.status === 'completed') {
                    updateProgress(100, 'done', 'Selesai!');
                    eventSource.close();
                    setTimeout(() => showCompleted(data), 600);
                } else if (data.status === 'error') {
                    eventSource.close();
                    showError(data.message || 'Terjadi kesalahan.');
                }
            } catch (e) {
                console.error('Error parsing SSE:', e);
            }
        };

        eventSource.onerror = (err) => {
            console.warn('SSE stream closed or error:', err);
        };
    }

    // ─── Show Completed ───
    function showCompleted(data) {
        progressCard.classList.add('hidden');
        const mode = data.process_mode || currentMode;
        const judul = data.judul || 'Kajian Islam';

        if (mode === 'transcript') {
            // Show transcript-only result card
            resultCard.classList.remove('hidden');
            resultDocTitle.textContent = judul;
            btnDownload.href = data.download_url;
            btnSummaryDownload.classList.add('hidden');
            if (btnBundleDownload) btnBundleDownload.classList.add('hidden');
            setTimeout(() => autoDownload(data.download_url, 'Transkrip_Kajian.pdf'), 300);

        } else if (mode === 'summary') {
            // Show summary result card
            summaryResultCard.classList.remove('hidden');
            summaryDocTitle.textContent = judul;
            summaryModeLabel.textContent = 'Ringkasan Kajian Siap';
            btnTranscriptFromSummary.classList.add('hidden');
            if (btnBundleDlSummary) btnBundleDlSummary.classList.add('hidden');
            if (data.summary_download_url) {
                btnSummaryDlMain.href = data.summary_download_url;
                btnSummaryDlMain.classList.remove('hidden');
                setTimeout(() => autoDownload(data.summary_download_url, 'Ringkasan_Kajian.pdf'), 300);
            }
            if (data.summary_data) {
                currentSummaryData = data.summary_data;
                populateSummaryUI(data.summary_data);
            }

        } else if (mode === 'both') {
            // Mode Lengkap Keduanya
            resultCard.classList.remove('hidden');
            resultDocTitle.textContent = judul;

            // Highlight: Bundle Complete PDF
            if (data.bundle_download_url && btnBundleDownload) {
                btnBundleDownload.href = data.bundle_download_url;
                btnBundleDownload.classList.remove('hidden');
            }
            if (data.download_url) {
                btnDownload.href = data.download_url;
            }
            if (data.summary_download_url) {
                btnSummaryDownload.href = data.summary_download_url;
                btnSummaryDownload.classList.remove('hidden');
            }

            // Summary card below
            if (data.summary_data) {
                summaryResultCard.classList.remove('hidden');
                summaryDocTitle.textContent = judul;
                summaryModeLabel.textContent = 'Ringkasan & Transkrip Lengkap Siap';
                currentSummaryData = data.summary_data;
                populateSummaryUI(data.summary_data);

                if (data.bundle_download_url && btnBundleDlSummary) {
                    btnBundleDlSummary.href = data.bundle_download_url;
                    btnBundleDlSummary.classList.remove('hidden');
                }
                if (data.summary_download_url) {
                    btnSummaryDlMain.href = data.summary_download_url;
                    btnSummaryDlMain.classList.remove('hidden');
                }
                if (data.download_url) {
                    btnTranscriptFromSummary.href = data.download_url;
                    btnTranscriptFromSummary.classList.remove('hidden');
                }
            }

            // Automatically download the unified complete PDF booklet
            const targetUrl = data.bundle_download_url || data.download_url;
            if (targetUrl) {
                setTimeout(() => autoDownload(targetUrl, 'Kajian_Lengkap.pdf'), 300);
            }
        }
    }

    function autoDownload(url, filename) {
        if (!url) return;
        const tempLink = document.createElement('a');
        tempLink.href = url;
        const safeName = (filename || 'Dokumen_Kajian.pdf').endsWith('.pdf')
            ? (filename || 'Dokumen_Kajian.pdf')
            : `${filename || 'Dokumen_Kajian'}.pdf`;
        tempLink.setAttribute('download', safeName);
        document.body.appendChild(tempLink);
        tempLink.click();
        document.body.removeChild(tempLink);
    }

    // ─── Populate Summary UI ───
    function populateSummaryUI(data) {
        // Quick Takeaways
        const takeawayList = document.getElementById('takeaway-list');
        takeawayList.innerHTML = '';
        (data.quick_takeaways || []).forEach((point, i) => {
            const li = document.createElement('li');
            li.innerHTML = `<span class="takeaway-num">${i + 1}</span><span>${escHtml(point)}</span>`;
            takeawayList.appendChild(li);
        });

        const materiContent = document.getElementById('materi-content');
        materiContent.innerHTML = '';
        const refleksiContent = document.getElementById('refleksi-content');
        refleksiContent.innerHTML = '';
        const korList = document.getElementById('korelasi-dalil-list');
        korList.innerHTML = '';
        const aksiList = document.getElementById('aksi-list');
        aksiList.innerHTML = '';

        if (data.integrated_sections && data.integrated_sections.length > 0) {
            // Render integrated sections in Materi tab
            data.integrated_sections.forEach((sec, idx) => {
                const card = document.createElement('div');
                card.className = 'integrated-module-card';

                let html = `
                    <div class="integrated-module-header">
                        <span class="integrated-module-badge">MODUL ${idx + 1}</span>
                        <span class="integrated-module-title">${escHtml(sec.sub_judul)}</span>
                    </div>
                `;

                // Ringkasan
                const ringkasanText = sec.ringkasan || sec.konten || '';
                if (ringkasanText) {
                    html += `<div class="module-sublabel">📖 RINGKASAN MATERI</div>
                             <div class="materi-section-text">${renderKontenToHTML(ringkasanText)}</div>`;
                }

                // Refleksi
                if (sec.refleksi) {
                    const rParas = sec.refleksi.split('\n').filter(p => p.trim());
                    html += `<div class="module-sublabel" style="color:#0284c7;margin-top:16px;">🌐 REFLEKSI ZAMAN NOW</div>
                             <div class="refleksi-box">${rParas.map(p => `<p>${escHtml(p)}</p>`).join('')}</div>`;
                }

                // Dalil Pendukung
                if (sec.dalil_pendukung && sec.dalil_pendukung.length > 0) {
                    html += `<div class="module-sublabel" style="color:var(--gold);margin-top:16px;">📚 DALIL PENDUKUNG TERKAIT</div>
                             <div class="korelasi-dalil-list">`;
                    sec.dalil_pendukung.forEach(item => {
                        html += `
                            <div class="korelasi-item">
                                ${item.sumber ? `<div class="korelasi-sumber">🔖 ${escHtml(item.sumber)}</div>` : ''}
                                ${item.arab ? `<div class="korelasi-arabic">${escHtml(item.arab)}</div>` : ''}
                                ${item.terjemahan ? `<div class="korelasi-terjemahan">"${escHtml(item.terjemahan)}"</div>` : ''}
                                ${item.relevansi ? `<div class="korelasi-relevansi">→ ${escHtml(item.relevansi)}</div>` : ''}
                                ${item.lathaif ? `<div class="korelasi-relevansi" style="color:#c4b5fd">✨ Lathaif: ${escHtml(item.lathaif)}</div>` : ''}
                            </div>
                        `;
                    });
                    html += `</div>`;
                }

                // Tadabbur & Lathaif
                if (sec.tadabbur_lathaif && sec.tadabbur_lathaif.length > 0) {
                    html += `<div class="module-sublabel" style="color:#c084fc;margin-top:16px;">🔍 TADABBUR & LATHAIF (TAFSIR TAHLILY)</div>`;
                    sec.tadabbur_lathaif.forEach(t => {
                        html += `
                            <div class="tadabbur-box">
                                <div class="tadabbur-badge">💎 DETAIL TADABBUR & LATHAIF</div>
                                ${t.fokus_dalil ? `<div class="tadabbur-fokus">📖 <b>Lafadz/Ayat:</b> ${escHtml(t.fokus_dalil)}</div>` : ''}
                                ${t.tinjauan_bahasa ? `<div class="tadabbur-bahasa">🔤 <b>Bahasa & Balaghah:</b> ${escHtml(t.tinjauan_bahasa)}</div>` : ''}
                                ${t.lathaif_hikmah ? `<div class="tadabbur-hikmah">✨ <b>Lathaif & Rahasia Makna:</b> ${escHtml(t.lathaif_hikmah)}</div>` : ''}
                            </div>
                        `;
                    });
                }

                // Aksi Nyata
                if (sec.aksi_nyata && sec.aksi_nyata.length > 0) {
                    html += `<div class="module-sublabel" style="color:var(--primary);margin-top:16px;">✅ AKSI NYATA TERUKUR</div>
                             <ul class="aksi-list">`;
                    sec.aksi_nyata.forEach(a => {
                        html += `<li><span class="aksi-check">✅</span><span>${escHtml(a)}</span></li>`;
                    });
                    html += `</ul>`;
                }

                card.innerHTML = html;
                materiContent.appendChild(card);

                // Populate other tabs as aggregated view
                if (sec.refleksi) {
                    const rDiv = document.createElement('div');
                    rDiv.className = 'refleksi-box';
                    rDiv.style.marginBottom = '14px';
                    rDiv.innerHTML = `<div style="font-weight:700;color:#38bdf8;margin-bottom:8px">📌 Modul ${idx + 1}: ${escHtml(sec.sub_judul)}</div>` +
                                     sec.refleksi.split('\n').filter(p => p.trim()).map(p => `<p>${escHtml(p)}</p>`).join('');
                    refleksiContent.appendChild(rDiv);
                }

                if (sec.dalil_pendukung) {
                    sec.dalil_pendukung.forEach(item => {
                        const div = document.createElement('div');
                        div.className = 'korelasi-item';
                        div.innerHTML = `
                            <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:4px">Modul ${idx + 1}: ${escHtml(sec.sub_judul)}</div>
                            ${item.sumber ? `<div class="korelasi-sumber">🔖 ${escHtml(item.sumber)}</div>` : ''}
                            ${item.arab ? `<div class="korelasi-arabic">${escHtml(item.arab)}</div>` : ''}
                            ${item.terjemahan ? `<div class="korelasi-terjemahan">"${escHtml(item.terjemahan)}"</div>` : ''}
                            ${item.relevansi ? `<div class="korelasi-relevansi">→ ${escHtml(item.relevansi)}</div>` : ''}
                        `;
                        korList.appendChild(div);
                    });
                }

                if (sec.aksi_nyata) {
                    sec.aksi_nyata.forEach(a => {
                        const li = document.createElement('li');
                        li.innerHTML = `<span class="aksi-check">✅</span><span><b>[Modul ${idx + 1}]</b> ${escHtml(a)}</span>`;
                        aksiList.appendChild(li);
                    });
                }
            });
        } else {
            // Legacy rendering
            (data.ringkasan_sections || []).forEach((sec, idx) => {
                const div = document.createElement('div');
                div.className = 'materi-section';
                div.innerHTML = `<div class="materi-section-title">${idx + 1}. ${escHtml(sec.sub_judul)}</div>
                                 <div class="materi-section-text">${renderKontenToHTML(sec.konten)}</div>`;
                materiContent.appendChild(div);
            });

            const paragraphs = (data.refleksi_zaman_now || '').split('\n').filter(p => p.trim());
            refleksiContent.innerHTML = paragraphs.map(p => `<p>${escHtml(p)}</p>`).join('');

            (data.korelasi_dalil_terkait || []).forEach(item => {
                const div = document.createElement('div');
                div.className = 'korelasi-item';
                div.innerHTML = `
                    ${item.sumber ? `<div class="korelasi-sumber">🔖 ${escHtml(item.sumber)}</div>` : ''}
                    ${item.arab ? `<div class="korelasi-arabic">${escHtml(item.arab)}</div>` : ''}
                    ${item.terjemahan ? `<div class="korelasi-terjemahan">"${escHtml(item.terjemahan)}"</div>` : ''}
                    ${item.relevansi ? `<div class="korelasi-relevansi">→ ${escHtml(item.relevansi)}</div>` : ''}
                `;
                korList.appendChild(div);
            });

            (data.aksi_nyata || []).forEach((aksi) => {
                const li = document.createElement('li');
                li.innerHTML = `<span class="aksi-check">✅</span><span>${escHtml(aksi)}</span>`;
                aksiList.appendChild(li);
            });
        }

        // Store for copy function
        currentSummaryData = data;
    }

    // Render konten with [DALIL] / [TERJEMAHAN] markers to HTML
    function renderKontenToHTML(konten) {
        if (!konten) return '';
        const dalilPattern = /(\[DALIL\][\s\S]*?\[\/DALIL\]|\[TERJEMAHAN\][\s\S]*?\[\/TERJEMAHAN\])/gi;
        const parts = konten.split(dalilPattern);
        return parts.map(part => {
            if (/^\[DALIL\]/i.test(part)) {
                const text = part.replace(/^\[DALIL\]/i, '').replace(/\[\/DALIL\]$/i, '').trim();
                return `<div class="dalil-box">
                    <div class="dalil-badge">📖 DALIL (AYAT AL-QUR'AN / HADITS)</div>
                    <div class="dalil-arabic">${escHtml(text)}</div>
                </div>`;
            } else if (/^\[TERJEMAHAN\]/i.test(part)) {
                const text = part.replace(/^\[TERJEMAHAN\]/i, '').replace(/\[\/TERJEMAHAN\]$/i, '').trim();
                return `<div class="dalil-terjemahan">"${escHtml(text)}"</div>`;
            } else {
                return part.split('\n').filter(p => p.trim())
                    .map(p => `<p style="margin-bottom:6px;font-size:0.92rem;color:var(--text-muted);line-height:1.6">${escHtml(p)}</p>`)
                    .join('');
            }
        }).join('');
    }

    // ─── Mermaid Diagram Rendering ───
    async function renderMermaid(diagramCode) {
        const container = document.getElementById('mermaid-diagram');
        if (!container || !diagramCode) return;
        container.innerHTML = diagramCode;
        container.removeAttribute('data-processed');
        try {
            await mermaid.run({ nodes: [container] });
        } catch (e) {
            console.warn('Mermaid render error:', e);
            container.innerHTML = `<p style="color:var(--text-dim);font-size:0.85rem;">Bagan tidak dapat dirender. Data: <pre style="text-align:left;font-size:0.78rem;overflow:auto">${escHtml(diagramCode)}</pre></p>`;
        }
    }

    // ─── Copy to Clipboard (WA Format) ───
    btnCopySummary.addEventListener('click', () => {
        if (!currentSummaryData) return;
        const text = buildCopyText(currentSummaryData);
        navigator.clipboard.writeText(text).then(() => {
            btnCopySummary.classList.add('copied');
            btnCopySummary.querySelector('span').textContent = '✓ Tersalin ke Clipboard!';
            setTimeout(() => {
                btnCopySummary.classList.remove('copied');
                btnCopySummary.querySelector('span').textContent = 'Salin Ringkasan (WA/Copas)';
            }, 2500);
        }).catch(() => {
            alert('Gagal menyalin. Coba secara manual.');
        });
    });

    function buildCopyText(data) {
        const lines = [];
        lines.push(`📚 *${data.judul_kajian || 'Ringkasan Kajian'}*`);
        lines.push('');

        // Quick Takeaways
        lines.push('⚡ *INTISARI CEPAT:*');
        (data.quick_takeaways || []).forEach((p, i) => lines.push(`${i + 1}. ${p}`));
        lines.push('');

        if (data.integrated_sections && data.integrated_sections.length > 0) {
            data.integrated_sections.forEach((sec, i) => {
                lines.push('━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
                lines.push(`📌 *MODUL ${i + 1}: ${(sec.sub_judul || '').toUpperCase()}*`);
                lines.push('━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

                const cleanKonten = (sec.ringkasan || sec.konten || '')
                    .replace(/\[DALIL\]/gi, '\n🔷 ').replace(/\[\/DALIL\]/gi, '')
                    .replace(/\[TERJEMAHAN\]/gi, '_(').replace(/\[\/TERJEMAHAN\]/gi, ')_')
                    .trim();
                lines.push(cleanKonten);
                lines.push('');

                if (sec.refleksi) {
                    lines.push('🌐 *Refleksi Zaman Now:*');
                    lines.push(sec.refleksi);
                    lines.push('');
                }

                if (sec.dalil_pendukung && sec.dalil_pendukung.length > 0) {
                    lines.push('📚 *Dalil Pendukung:*');
                    sec.dalil_pendukung.forEach((d, di) => {
                        lines.push(`${di + 1}. ${d.sumber || 'Dalil'}: "${d.terjemahan || ''}"`);
                        if (d.relevansi) lines.push(`   → ${d.relevansi}`);
                    });
                    lines.push('');
                }

                if (sec.tadabbur_lathaif && sec.tadabbur_lathaif.length > 0) {
                    lines.push('🔍 *Tadabbur & Lathaif (Tafsir Tahlily):*');
                    sec.tadabbur_lathaif.forEach((t) => {
                        if (t.fokus_dalil) lines.push(`• Fokus: ${t.fokus_dalil}`);
                        if (t.tinjauan_bahasa) lines.push(`  🔤 Bahasa: ${t.tinjauan_bahasa}`);
                        if (t.lathaif_hikmah) lines.push(`  ✨ Lathaif: ${t.lathaif_hikmah}`);
                    });
                    lines.push('');
                }

                if (sec.aksi_nyata && sec.aksi_nyata.length > 0) {
                    lines.push('✅ *Aksi Nyata:*');
                    sec.aksi_nyata.forEach((a, ai) => lines.push(`  ${ai + 1}. ${a}`));
                    lines.push('');
                }
            });
        } else {
            // Legacy copy format
            lines.push('📖 *RINGKASAN MATERI:*');
            (data.ringkasan_sections || []).forEach((sec, i) => {
                lines.push(`\n*${i + 1}. ${sec.sub_judul}*`);
                const cleanKonten = (sec.konten || '')
                    .replace(/\[DALIL\]/gi, '\n🔷 ').replace(/\[\/DALIL\]/gi, '')
                    .replace(/\[TERJEMAHAN\]/gi, '_(').replace(/\[\/TERJEMAHAN\]/gi, ')_')
                    .trim();
                lines.push(cleanKonten);
            });
            lines.push('');

            lines.push('🌐 *REFLEKSI ZAMAN NOW:*');
            lines.push(data.refleksi_zaman_now || '');
            lines.push('');

            lines.push('✅ *AKSI NYATA:*');
            (data.aksi_nyata || []).forEach((a, i) => lines.push(`${i + 1}. ${a}`));
            lines.push('');
        }

        lines.push('_Diproses oleh Kajian Transcriber 📱_');
        return lines.join('\n');
    }

    // ─── Progress ───
    function updateProgress(percent, stage, message) {
        const p = Math.max(0, Math.min(100, Math.round(percent)));
        progressPercentLabel.textContent = `${p}%`;
        progressFillBar.style.width = `${p}%`;
        progressStatusDetail.textContent = message;
        updateStepper(stage, p);
    }

    function updateStepper(stage, percent) {
        const steps = [1,2,3,4,5].map(i => document.getElementById(`step-${i}`));
        const lines = [1,2,3,4].map(i => document.getElementById(`line-${i}`));

        steps.forEach(s => s && (s.className = 'step-item'));
        lines.forEach(l => l && (l.className = 'step-line'));

        const done = (arr) => arr.forEach(el => el && el.classList.add('done'));
        const active = (el) => el && el.classList.add('active');

        if (stage === 'download' || stage === 'upload') {
            active(steps[0]);
        } else if (stage === 'convert') {
            done([steps[0], lines[0]]); active(steps[1]);
        } else if (stage === 'transcribe') {
            done([steps[0], lines[0], steps[1], lines[1]]); active(steps[2]);
        } else if (stage === 'dalil' || stage === 'summary') {
            done([steps[0], lines[0], steps[1], lines[1], steps[2], lines[2]]); active(steps[3]);
        } else if (stage === 'pdf' || stage === 'done') {
            done([steps[0], lines[0], steps[1], lines[1], steps[2], lines[2], steps[3], lines[3]]);
            stage === 'done' ? done([steps[4]]) : active(steps[4]);
        }
    }

    function resetStepper() {
        for (let i = 1; i <= 5; i++) {
            const s = document.getElementById(`step-${i}`);
            if (s) s.className = 'step-item';
        }
        for (let i = 1; i <= 4; i++) {
            const l = document.getElementById(`line-${i}`);
            if (l) l.className = 'step-line';
        }
    }

    function showError(message) {
        progressCard.classList.add('hidden');
        resultCard.classList.add('hidden');
        summaryResultCard.classList.add('hidden');
        errorCard.classList.remove('hidden');
        errorMessageText.textContent = message || 'Terjadi kesalahan internal.';
    }

    function resetAll() {
        [resultCard, summaryResultCard, errorCard, progressCard].forEach(c => c.classList.add('hidden'));
        if (btnBundleDownload) btnBundleDownload.classList.add('hidden');
        if (btnBundleDlSummary) btnBundleDlSummary.classList.add('hidden');
        if (btnSummaryDownload) btnSummaryDownload.classList.add('hidden');
        if (btnSummaryDlMain) btnSummaryDlMain.classList.add('hidden');
        if (btnTranscriptFromSummary) btnTranscriptFromSummary.classList.add('hidden');
        formCard.classList.remove('hidden');
        transcribeForm.reset();
        selectedFileName.classList.add('hidden');
        currentSummaryData = null;
        // Reset mode cards
        modeCards.forEach(c => c.classList.remove('active'));
        document.querySelector('.mode-card[data-mode="transcript"]').classList.add('active');
        currentMode = 'transcript';
        processModeInput.value = 'transcript';
    }

    // ─── Restart Buttons ───
    btnRestart.addEventListener('click', resetAll);
    btnRestartSummary.addEventListener('click', resetAll);
    btnErrorRetry.addEventListener('click', () => {
        errorCard.classList.add('hidden');
        formCard.classList.remove('hidden');
    });

    // ─── Utilities ───
    function escHtml(str) {
        if (!str) return '';
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
});
