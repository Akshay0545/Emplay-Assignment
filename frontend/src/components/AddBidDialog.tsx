import { useCallback, useId, useRef, useState, type DragEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { FileText, Loader2, Upload, X } from "lucide-react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { bidsQueryKey } from "@/hooks/useBids";
import { cn } from "@/lib/utils";

const BID_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const ACCEPTED = [".pdf", ".html", ".htm"];

/** File size as a short human string, e.g. "1.2 MB". */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isAccepted(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED.some((ext) => name.endsWith(ext));
}

interface AddBidDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Upload files for a new bid, index it, then offer to run extraction. */
export function AddBidDialog({ open, onOpenChange }: AddBidDialogProps) {
  const [bidId, setBidId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const addFiles = useCallback((incoming: FileList | File[]) => {
    const list = Array.from(incoming);
    const rejected = list.filter((f) => !isAccepted(f));
    if (rejected.length > 0) {
      toast.error(`Only PDF and HTML files are accepted (${rejected.length} skipped).`);
    }
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name));
      return [...prev, ...list.filter((f) => isAccepted(f) && !seen.has(f.name))];
    });
  }, []);

  const upload = useMutation({
    mutationFn: () => api.uploadBid(bidId.trim(), files),
    // The server replies once the files are on disk; parsing and embedding run
    // in the background, so the dialog closes straight away and the new card
    // shows "indexing" until it is ready.
    onSuccess: (started) => {
      void queryClient.invalidateQueries({ queryKey: bidsQueryKey });
      const id = started.bid_id;
      toast.success(`Uploaded ${started.documents} file${started.documents === 1 ? "" : "s"} to ${id}`, {
        description: "Indexing in the background; the extraction starts on its own when it finishes.",
        action: {
          label: "Open bid",
          onClick: () => navigate(`/bids/${encodeURIComponent(id)}`),
        },
        duration: 8_000,
      });
      reset();
      onOpenChange(false);
    },
    onError: (err) => toast.error(err.message),
  });

  /** Closing mid-upload is allowed: only the file transfer is still in flight. */
  const closeDialog = (next: boolean) => {
    if (!next && upload.isPending) toast.info("Upload continues in the background");
    onOpenChange(next);
  };

  const reset = () => {
    setBidId("");
    setFiles([]);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
  };

  const validId = BID_ID_PATTERN.test(bidId.trim());
  const canSubmit = validId && files.length > 0 && !upload.isPending;

  return (
    <Dialog open={open} onOpenChange={closeDialog}>
      <DialogContent>
        <DialogHeader className="pr-6">
          <DialogTitle>Add bid</DialogTitle>
          <DialogDescription>
            Choose a bid id and drop the RFP, addenda and forms (PDF or HTML). Indexing starts as soon
            as the files are uploaded and the extraction follows automatically, both in the background.
          </DialogDescription>
        </DialogHeader>
        <form
          className="min-w-0 space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (canSubmit) upload.mutate();
          }}
        >
          <div className="space-y-1.5">
            <label htmlFor={inputId} className="text-sm font-medium">
              Bid id
            </label>
            <Input
              id={inputId}
              value={bidId}
              onChange={(e) => setBidId(e.target.value)}
              placeholder="letters, digits, _ or -"
              autoComplete="off"
              aria-invalid={bidId.length > 0 && !validId}
            />
            {bidId.length > 0 && !validId && (
              <p className="text-xs text-red-600 dark:text-red-400">
                Use 1-64 characters: letters, digits, underscore or dash.
              </p>
            )}
          </div>

          <div
            role="button"
            tabIndex={0}
            aria-label="Drop files here or press Enter to choose files"
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                inputRef.current?.click();
              }
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-5 text-center sm:p-6 text-sm text-muted-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              dragging ? "border-primary bg-accent" : "hover:border-primary/60",
            )}
          >
            <Upload className="h-6 w-6" />
            <span><span className="hidden sm:inline">Drag and drop files here, or click</span><span className="sm:hidden">Tap</span> to browse</span>
            <span className="text-xs">PDF, HTML</span>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={ACCEPTED.join(",")}
              className="hidden"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          {files.length > 0 && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {files.length} file{files.length === 1 ? "" : "s"} selected
                </span>
                <button
                  type="button"
                  className="rounded px-1 hover:text-foreground"
                  onClick={() => setFiles([])}
                  disabled={upload.isPending}
                >
                  Clear all
                </button>
              </div>
              <ul className="max-h-48 space-y-1 overflow-y-auto pr-1 text-sm">
                {files.map((f) => (
                  <li key={f.name} className="flex min-w-0 items-center gap-2 rounded-md bg-muted px-2.5 py-1.5">
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate" title={f.name}>
                      {f.name}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{formatSize(f.size)}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${f.name}`}
                      className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
                      onClick={() => setFiles((prev) => prev.filter((x) => x.name !== f.name))}
                      disabled={upload.isPending}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => closeDialog(false)}>
              {upload.isPending ? "Close" : "Cancel"}
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {upload.isPending && <Loader2 className="animate-spin" />}
              {upload.isPending ? "Uploading..." : `Upload ${files.length || ""} file${files.length === 1 ? "" : "s"}`}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
