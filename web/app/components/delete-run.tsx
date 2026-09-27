import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Spinner } from "~/components/ui/spinner";
import { api, type RunSummary } from "~/lib/api";

/** Confirms, then deletes a finished run's report and artifacts. */
export function DeleteRunDialog({
  run,
  onOpenChange,
  onDeleted,
}: {
  /** The run to delete; the dialog is open while this is set. */
  run: RunSummary | null;
  onOpenChange: (open: boolean) => void;
  onDeleted?: (run: RunSummary) => void;
}) {
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: (target: RunSummary) => api.deleteRun(target.id),
    onSuccess: (_, target) => {
      client.setQueryData<RunSummary[]>(["runs"], (runs) =>
        runs?.filter((item) => item.id !== target.id),
      );
      client.removeQueries({ queryKey: ["run", target.id] });
      client.invalidateQueries({ queryKey: ["runs"] });
      toast.success("Run deleted", { description: target.id });
      onOpenChange(false);
      onDeleted?.(target);
    },
    onError: (error) =>
      toast.error("Could not delete the run", { description: error.message }),
  });
  return (
    <AlertDialog
      open={!!run}
      onOpenChange={(open) => {
        if (!remove.isPending) onOpenChange(open);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this run?</AlertDialogTitle>
          <AlertDialogDescription>
            The report and artifacts of{" "}
            <span className="font-mono text-foreground">{run?.id}</span> are
            removed from <span className="font-mono">.regress/runs</span>. The
            test file it wrote stays as it is.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={remove.isPending}
            onClick={(event) => {
              // Keep the dialog open until the request settles.
              event.preventDefault();
              if (run) remove.mutate(run);
            }}
          >
            {remove.isPending && <Spinner data-icon="inline-start" />}
            Delete run
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
