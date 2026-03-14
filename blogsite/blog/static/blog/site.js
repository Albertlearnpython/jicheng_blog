(() => {
    const body = document.body;
    if (!body) {
        return;
    }

    const storageKeys = {
        theme: "blog.theme",
        font: "blog.font",
        density: "blog.density",
        sound: "blog.sound",
    };

    const defaults = {
        theme: "paper",
        font: "sans",
        density: "comfortable",
        sound: "on",
    };

    const storage = {
        get(key) {
            try {
                return localStorage.getItem(key);
            } catch (error) {
                return null;
            }
        },
        set(key, value) {
            try {
                localStorage.setItem(key, value);
            } catch (error) {
                return;
            }
        },
    };

    const applySetting = (type, value) => {
        body.dataset[type] = value;
        storage.set(storageKeys[type], value);
        document.querySelectorAll(`[data-${type}-value]`).forEach((button) => {
            button.classList.toggle("is-active", button.dataset[`${type}Value`] === value);
        });
    };

    Object.entries(defaults)
        .filter(([type]) => type !== "sound")
        .forEach(([type, value]) => {
            applySetting(type, storage.get(storageKeys[type]) || value);
        });

    let audioContext = null;

    const getAudioContext = () => {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) {
            return null;
        }
        if (!audioContext) {
            try {
                audioContext = new Context();
            } catch (error) {
                return null;
            }
        }
        return audioContext;
    };

    const soundButtons = document.querySelectorAll("[data-sound-toggle]");

    const isSoundEnabled = () => body.dataset.sound !== "off";

    const renderSoundState = (enabled) => {
        body.dataset.sound = enabled ? "on" : "off";
        storage.set(storageKeys.sound, body.dataset.sound);
        soundButtons.forEach((button) => {
            button.classList.toggle("is-active", enabled);
            button.setAttribute("aria-pressed", String(enabled));
            button.setAttribute("aria-label", enabled ? "关闭音效" : "开启音效");
            button.title = enabled ? "关闭音效" : "开启音效";
        });
    };

    const scheduleTone = ({ start = 0, duration = 0.06, frequency = 660, frequencyEnd = null, gain = 0.035, type = "triangle" }) => {
        const context = getAudioContext();
        if (!context) {
            return;
        }

        const now = context.currentTime + start;
        const oscillator = context.createOscillator();
        const amplifier = context.createGain();

        oscillator.type = type;
        oscillator.frequency.setValueAtTime(frequency, now);
        if (frequencyEnd) {
            oscillator.frequency.exponentialRampToValueAtTime(Math.max(frequencyEnd, 1), now + duration);
        }

        amplifier.gain.setValueAtTime(0.0001, now);
        amplifier.gain.exponentialRampToValueAtTime(gain, now + 0.01);
        amplifier.gain.exponentialRampToValueAtTime(0.0001, now + duration);

        oscillator.connect(amplifier);
        amplifier.connect(context.destination);
        oscillator.start(now);
        oscillator.stop(now + duration + 0.02);
    };

    const playUiSound = (preset) => {
        if (!isSoundEnabled()) {
            return;
        }

        const context = getAudioContext();
        if (!context) {
            return;
        }

        if (context.state === "suspended") {
            context.resume().catch(() => undefined);
        }

        if (preset === "switch") {
            scheduleTone({ frequency: 520, frequencyEnd: 620, duration: 0.06, gain: 0.026, type: "square" });
            scheduleTone({ start: 0.04, frequency: 780, frequencyEnd: 980, duration: 0.08, gain: 0.022, type: "triangle" });
            return;
        }

        if (preset === "open") {
            scheduleTone({ frequency: 300, frequencyEnd: 380, duration: 0.08, gain: 0.024, type: "triangle" });
            scheduleTone({ start: 0.05, frequency: 540, frequencyEnd: 720, duration: 0.09, gain: 0.02, type: "triangle" });
            return;
        }

        if (preset === "close") {
            scheduleTone({ frequency: 560, frequencyEnd: 420, duration: 0.08, gain: 0.02, type: "triangle" });
            scheduleTone({ start: 0.03, frequency: 360, frequencyEnd: 280, duration: 0.08, gain: 0.016, type: "square" });
            return;
        }

        if (preset === "tab") {
            scheduleTone({ frequency: 620, frequencyEnd: 760, duration: 0.05, gain: 0.022, type: "triangle" });
            scheduleTone({ start: 0.03, frequency: 880, frequencyEnd: 1040, duration: 0.06, gain: 0.018, type: "triangle" });
            return;
        }

        if (preset === "confirm") {
            scheduleTone({ frequency: 460, frequencyEnd: 520, duration: 0.05, gain: 0.022, type: "square" });
            scheduleTone({ start: 0.03, frequency: 740, frequencyEnd: 880, duration: 0.08, gain: 0.024, type: "triangle" });
            return;
        }

        if (preset === "enable") {
            scheduleTone({ frequency: 480, frequencyEnd: 620, duration: 0.06, gain: 0.024, type: "triangle" });
            scheduleTone({ start: 0.05, frequency: 760, frequencyEnd: 980, duration: 0.09, gain: 0.02, type: "triangle" });
            return;
        }

        if (preset === "disable") {
            scheduleTone({ frequency: 720, frequencyEnd: 560, duration: 0.07, gain: 0.02, type: "triangle" });
            scheduleTone({ start: 0.04, frequency: 420, frequencyEnd: 320, duration: 0.08, gain: 0.016, type: "square" });
            return;
        }

        scheduleTone({ frequency: 520, frequencyEnd: 460, duration: 0.05, gain: 0.018, type: "square" });
        scheduleTone({ start: 0.03, frequency: 720, frequencyEnd: 640, duration: 0.05, gain: 0.016, type: "triangle" });
    };

    renderSoundState((storage.get(storageKeys.sound) || defaults.sound) !== "off");

    document.querySelectorAll("[data-theme-value]").forEach((button) => {
        button.addEventListener("click", () => applySetting("theme", button.dataset.themeValue));
    });

    document.querySelectorAll("[data-font-value]").forEach((button) => {
        button.addEventListener("click", () => applySetting("font", button.dataset.fontValue));
    });

    document.querySelectorAll("[data-density-value]").forEach((button) => {
        button.addEventListener("click", () => applySetting("density", button.dataset.densityValue));
    });

    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
        button.addEventListener("click", () => {
            const nextTheme = body.dataset.theme === "midnight" ? "paper" : "midnight";
            applySetting("theme", nextTheme);
        });
    });

    soundButtons.forEach((button) => {
        button.addEventListener("click", () => {
            if (isSoundEnabled()) {
                playUiSound("disable");
                window.setTimeout(() => renderSoundState(false), 60);
                return;
            }

            renderSoundState(true);
            playUiSound("enable");
        });
    });

    const closeSidebar = () => body.classList.remove("sidebar-open");
    document.querySelectorAll("[data-sidebar-toggle]").forEach((button) => {
        button.addEventListener("click", () => body.classList.toggle("sidebar-open"));
    });
    document.querySelectorAll("[data-sidebar-overlay]").forEach((overlay) => {
        overlay.addEventListener("click", () => {
            closeSidebar();
            playUiSound("close");
        });
    });

    document.querySelectorAll("[data-tab-button]").forEach((button) => {
        button.addEventListener("click", () => {
            const target = button.dataset.target;
            document.querySelectorAll("[data-tab-button]").forEach((item) => {
                item.classList.toggle("is-active", item === button);
            });
            document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
                panel.classList.toggle("is-active", panel.dataset.tabPanel === target);
            });
        });
    });

    document.addEventListener(
        "click",
        (event) => {
            const trigger = event.target.closest("a, button, summary");
            if (!trigger || trigger.matches("[data-sound-toggle]")) {
                return;
            }

            if (trigger.matches(":disabled, [aria-disabled='true']")) {
                return;
            }

            let preset = "click";
            if (trigger.matches("[data-theme-toggle], [data-theme-value], [data-font-value], [data-density-value]")) {
                preset = "switch";
            } else if (trigger.matches("[data-sidebar-toggle]")) {
                preset = body.classList.contains("sidebar-open") ? "open" : "close";
            } else if (trigger.matches("[data-tab-button], summary")) {
                preset = "tab";
            } else if (trigger.matches(".primary-button")) {
                preset = "confirm";
            }

            playUiSound(preset);
        },
        { passive: true }
    );

    const runtimeLabels = document.querySelectorAll("[data-runtime-label]");
    const siteStart = body.dataset.siteStart ? new Date(body.dataset.siteStart) : null;
    if (runtimeLabels.length && siteStart && !Number.isNaN(siteStart.getTime())) {
        const renderRuntime = () => {
            const diff = Date.now() - siteStart.getTime();
            const seconds = Math.max(0, Math.floor(diff / 1000));
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            runtimeLabels.forEach((label) => {
                label.textContent = `站点运行 ${days} 天 ${hours} 小时 ${minutes} 分钟`;
            });
        };
        renderRuntime();
        window.setInterval(renderRuntime, 60000);
    }

    const progressBar = document.querySelector("[data-reading-progress]");
    if (progressBar) {
        const updateProgress = () => {
            const scrollTop = window.scrollY;
            const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
            const ratio = scrollHeight > 0 ? Math.min(scrollTop / scrollHeight, 1) : 0;
            progressBar.style.width = `${ratio * 100}%`;
        };
        updateProgress();
        window.addEventListener("scroll", updateProgress, { passive: true });
    }

    const chatForm = document.querySelector("[data-chat-form]");
    if (!chatForm) {
        return;
    }

    const messages = document.querySelector("[data-chat-messages]");
    const status = document.querySelector("[data-chat-status]");
    const endpoint = chatForm.dataset.endpoint;
    const submitButton = chatForm.querySelector('button[type="submit"]');
    const textarea = chatForm.querySelector('textarea[name="message"]');

    const appendMessage = (role, text) => {
        const article = document.createElement("article");
        article.className = `message message-${role}`;
        article.innerHTML = `
            <div class="message-role">${role === "assistant" ? "Assistant" : "You"}</div>
            <div class="message-body"></div>
        `;
        article.querySelector(".message-body").textContent = text;
        messages.appendChild(article);
        messages.scrollTop = messages.scrollHeight;
    };

    const getCsrfToken = () => {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    };

    chatForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const formData = new FormData(chatForm);
        const message = String(formData.get("message") || "").trim();

        if (!message || !endpoint) {
            return;
        }

        appendMessage("user", message);
        textarea.value = "";
        if (status) {
            status.textContent = "请求中...";
        }
        if (submitButton) {
            submitButton.disabled = true;
        }

        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                },
                body: JSON.stringify({
                    message,
                    reasoning_effort: formData.get("reasoning_effort"),
                    verbosity: formData.get("verbosity"),
                }),
            });

            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || "请求失败");
            }

            appendMessage("assistant", payload.text || "接口没有返回文本内容。");
            if (status) {
                status.textContent = "已完成";
            }
        } catch (error) {
            appendMessage("assistant", error.message || "请求失败");
            if (status) {
                status.textContent = "请求失败";
            }
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
            }
        }
    });
})();
