(() => {
    const body = document.body;
    if (!body) {
        return;
    }

    const storageKeys = {
        theme: "blog.theme",
        font: "blog.font",
        density: "blog.density",
    };

    const defaults = {
        theme: "paper",
        font: "sans",
        density: "comfortable",
    };

    const applySetting = (type, value) => {
        body.dataset[type] = value;
        localStorage.setItem(storageKeys[type], value);
        document.querySelectorAll(`[data-${type}-value]`).forEach((button) => {
            button.classList.toggle("is-active", button.dataset[`${type}Value`] === value);
        });
    };

    Object.entries(defaults).forEach(([type, value]) => {
        applySetting(type, localStorage.getItem(storageKeys[type]) || value);
    });

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

    const closeSidebar = () => body.classList.remove("sidebar-open");
    document.querySelectorAll("[data-sidebar-toggle]").forEach((button) => {
        button.addEventListener("click", () => body.classList.toggle("sidebar-open"));
    });
    document.querySelectorAll("[data-sidebar-overlay]").forEach((overlay) => {
        overlay.addEventListener("click", closeSidebar);
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
