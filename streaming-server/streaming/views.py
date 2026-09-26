import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

USERNAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
MAX_IMAGE_BYTES = 8 * 1024 * 1024

def clean_username(value):
    value = USERNAME_RE.sub("_", value.strip())
    return value[:64].strip("_")

def login_view(request):
    if request.method == "POST":
        username = clean_username(request.POST.get("username", ""))
        if not username:
            return render(
                request,
                "streaming/login.html",
                {"error": "Please enter a valid username."},
                status=400,
            )
        request.session["username"] = username
        return redirect("streaming:camera")

    if request.session.get("username"):
        return redirect("streaming:camera")
    return render(request, "streaming/login.html")

def camera_view(request):
    username = request.session.get("username")
    if not username:
        return redirect("streaming:login")
    return render(request, "streaming/camera.html", {"username": username})

@require_POST
def snapshot_view(request):
    username = request.session.get("username")
    if not username:
        return JsonResponse({"error": "Session expired. Please sign in again."}, status=401)

    image = request.FILES.get("image")
    if image is None:
        return JsonResponse({"error": "No image was provided."}, status=400)

    if image.content_type not in {"image/jpeg", "image/jpg"}:
        return JsonResponse({"error": "Only JPEG snapshots are accepted."}, status=415)

    if image.size > MAX_IMAGE_BYTES:
        return JsonResponse({"error": "Snapshot is too large."}, status=413)

    if not settings.AWS_S3_BUCKET_NAME:
        return JsonResponse({"error": "S3 bucket is not configured."}, status=500)

    now = datetime.now(timezone.utc)
    object_name = f"{now:%Y-%m-%d}_{now:%H-%M-%S-%f}_{username}.jpeg"

    try:
        s3 = boto3.client("s3", region_name=settings.AWS_REGION)
        s3.upload_fileobj(
            image,
            settings.AWS_S3_BUCKET_NAME,
            object_name,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
    #except (BotoCoreError, ClientError):
    #    return JsonResponse({"error": "Unable to upload snapshot to S3."}, status=502)
    except (BotoCoreError, ClientError) as error:
        print("S3 UPLOAD ERROR:", repr(error))

        return JsonResponse(
            {
                "error": "Unable to upload snapshot to S3.",
                "details": str(error),
            },
            status=502,
        )

    return JsonResponse({"ok": True, "filename": object_name})

@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("streaming:login")
