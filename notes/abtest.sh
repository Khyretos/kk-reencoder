#!/bin/bash
# runs inside the container: /media ro, /work rw. One line per (file,q): file q vmaf_mean ratio
set -u
while IFS= read -r f; do
  src="/media/$f"; id=$(echo "$f" | md5sum | cut -c1-8)
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src" | cut -d. -f1)
  for pc in 20 50 80; do
    ss=$((dur*pc/100)); ffmpeg -nostdin -v error -y -ss $ss -i "$src" -t 20 -map 0:v:0 -c copy /work/$id-$pc.mkv && ffmpeg -nostdin -v error -y -i /work/$id-$pc.mkv -c:v ffv1 /work/$id-$pc-ref.mkv
  done
  for q in 14 18 22 26 30; do
    tv=0; tr=0; n=0
    for pc in 20 50 80; do
      s=/work/$id-$pc.mkv; o=/work/$id-$pc-q$q.mkv
      ffmpeg -nostdin -v error -y -init_hw_device qsv=hw:/dev/dri/renderD128 -i ${s%.mkv}-ref.mkv -vf format=p010le -c:v av1_qsv -preset veryslow -global_quality $q -extbrc 1 -look_ahead_depth 40 $o || continue
      v=$(nice -n19 ffmpeg -nostdin -i $o -i ${s%.mkv}-ref.mkv -lavfi "[0:v]setpts=PTS-STARTPTS,format=yuv420p[d];[1:v]setpts=PTS-STARTPTS,format=yuv420p[r];[d][r]libvmaf=n_threads=4:n_subsample=4" -f null - 2>&1 | grep -o 'VMAF score: [0-9.]*' | awk '{print $3}')
      r=$(echo "$(stat -c%s $o) $(stat -c%s $s)" | awk '{print $1/$2}')
      tv=$(echo "$tv $v" | awk '{print $1+$2}'); tr=$(echo "$tr $r" | awk '{print $1+$2}'); n=$((n+1)); rm -f $o
    done
    echo -e "$f\tq=$q\tvmaf=$(echo $tv $n | awk '{printf "%.2f",$1/$2}')\tsize=$(echo $tr $n | awk '{printf "%.3f",$1/$2}')"
  done
  rm -f /work/$id-*.mkv
done < /work/picks.txt
