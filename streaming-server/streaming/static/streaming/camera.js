(() => {
    const video = document.getElementById("cameraVideo");
    const canvas = document.getElementById("snapshotCanvas");
    const startButton = document.getElementById("startButton");
    const stopButton = document.getElementById("stopButton");
    const switchButton = document.getElementById("switchButton");
    const placeholder = document.getElementById("cameraPlaceholder");
    const statusText = document.getElementById("statusText");
    const statusDot = document.getElementById("statusDot");
    const uploadStatus = document.getElementById("uploadStatus");
    const csrfToken = document.getElementById("csrfToken").value;

    let stream = null;
    let snapshotTimer = null;
    let facingMode = "environment";
    let uploadInProgress = false;

    function setStatus(text, state = "idle") {
        statusText.textContent = text;
        statusDot.classList.remove("active", "error");
        if (state === "active") statusDot.classList.add("active");
        if (state === "error") statusDot.classList.add("error");
    }

    async function openCamera() {
        if (!navigator.mediaDevices?.getUserMedia) {
            throw new Error("This browser does not support camera access.");
        }

        stream = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: {
                facingMode: { ideal: facingMode },
                width: { ideal: 1280 },
                height: { ideal: 720 }
            }
        });

        video.srcObject = stream;
        await video.play();

        placeholder.hidden = true;
        startButton.disabled = true;
        stopButton.disabled = false;
        switchButton.disabled = false;

        setStatus("Streaming", "active");
        uploadStatus.textContent = "Camera started. Preparing snapshots...";

        snapshotTimer = window.setInterval(captureAndUpload, 3000);
    }

    function stopCamera() {
        if (snapshotTimer !== null) {
            window.clearInterval(snapshotTimer);
            snapshotTimer = null;
        }

        if (stream) {
            stream.getTracks().forEach(track => track.stop());
            stream = null;
        }

        video.srcObject = null;
        placeholder.hidden = false;
        startButton.disabled = false;
        stopButton.disabled = true;
        switchButton.disabled = true;

        setStatus("Camera stopped");
        uploadStatus.textContent =
            "Snapshots will upload every 3 seconds while streaming.";
    }

    async function switchCamera() {
        if (!stream) return;

        if (snapshotTimer !== null) {
            window.clearInterval(snapshotTimer);
            snapshotTimer = null;
        }

        stream.getTracks().forEach(track => track.stop());
        stream = null;

        facingMode = facingMode === "environment" ? "user" : "environment";

        try {
            await openCamera();
        } catch (error) {
            stopCamera();
            setStatus("Camera error", "error");
            uploadStatus.textContent = error.message;
        }
    }

    async function captureAndUpload() {
        if (!stream || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
        if (uploadInProgress) return;

        const width = video.videoWidth;
        const height = video.videoHeight;
        if (!width || !height) return;

        canvas.width = width;
        canvas.height = height;

        const context = canvas.getContext("2d");
        context.drawImage(video, 0, 0, width, height);

        const blob = await new Promise(resolve =>
            canvas.toBlob(resolve, "image/jpeg", 0.88)
        );

        if (!blob) return;

        const formData = new FormData();
        formData.append("image", blob, "snapshot.jpeg");

        uploadInProgress = true;
        uploadStatus.textContent = "Uploading snapshot...";

        try {
            const response = await fetch(window.vectorCompareConfig.snapshotUrl, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken
                },
                body: formData,
                credentials: "same-origin"
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || "Snapshot upload failed.");
            }

            uploadStatus.textContent = `Uploaded: ${data.filename}`;
        } catch (error) {
            uploadStatus.textContent = error.message;
        } finally {
            uploadInProgress = false;
        }
    }

    startButton.addEventListener("click", async () => {
        startButton.disabled = true;
        setStatus("Requesting camera permission...");

        try {
            await openCamera();
        } catch (error) {
            startButton.disabled = false;
            setStatus("Camera unavailable", "error");
            uploadStatus.textContent =
                "Unable to start the camera. Check browser permissions and HTTPS.";
        }
    });

    stopButton.addEventListener("click", stopCamera);
    switchButton.addEventListener("click", switchCamera);
    window.addEventListener("pagehide", stopCamera);
})();
