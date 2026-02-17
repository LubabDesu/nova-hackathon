import { useCallback } from "react";
import { useDropzone } from "react-dropzone";

interface IdeaDropzoneProps {
    onFilesAdded: (files: File[]) => void;
}

export default function IdeaDropzone({ onFilesAdded }: IdeaDropzoneProps) {
    // useCallback is heavily used with react-dropzone to efficiently handle the dropped files
    const onDrop = useCallback(
        (acceptedFiles: File[]) => {
            if (acceptedFiles.length > 0) {
                onFilesAdded(acceptedFiles);
            }
        },
        [onFilesAdded],
    );

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop,
        // You can strictly define and restrict the accepted file types here
        accept: {
            "image/*": [".jpeg", ".jpg", ".png", ".webp"],
            "video/*": [".mp4", ".mov", ".webm", ".m4v"],
        },
    });

    return (
        <div
            {...getRootProps()}
            className={`idea-dropzone ${isDragActive ? "is-drag-active" : ""}`}
        >
            <input {...getInputProps()} />
            <div className="idea-dropzone-copy">
                {isDragActive ? (
                    <p className="dropzone-active-text">
                        Drop your travel ideas here...
                    </p>
                ) : (
                    <div>
                        <p className="dropzone-title">
                            Drag & drop your travel ideas here
                        </p>
                        <p className="dropzone-hint">
                            Supports images and short videos
                        </p>
                    </div>
                )}
            </div>
        </div>
    );
}
