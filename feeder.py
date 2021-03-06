import tensorflow as tf
import numpy as np
from python_speech_features import logfbank
import random
import itertools
import pickle
import time
import re
import os
import glob
import argparse
from _thread import start_new_thread
import webrtcvad
import vad_ex


"""
input dir

vox1_dev_wav - id #### - 0DOmwbPlPvY - 00001.wav
                                     - 00002.wav
                                     - ...
                       - 5VNK93duiOM
                       - ...
                       
             - id #### - ...

"""

class Feeder():
    def __init__(self, hparams, mode, data_type=None):
        # Set hparams
        self.hparams = hparams
        self.mode = mode
        if self.mode == "train":
            assert data_type != None
            self.data_type = data_type
    def set_up_feeder(self, queue=None):

        if self.mode == "train":
            #pickles = ["id11251_gFfcgOVmiO0_00004.pickle", "id11251_gFfcgOVmiO0_00005.pickle"...]
            self.pickles = os.listdir(self.hparams.in_dir + "/" + self.data_type)
            self.spk_names = list(set([pickle.split("_")[0] for pickle in self.pickles]))
            # Create Queue
            #self.queue = queue.Queue()
            self.queue = queue
            # Start new thread
            start_new_thread(self.generate_data, ())

        elif self.mode == "infer":
            pass

        elif self.mode == "test":
            test_wavs = glob.glob(self.hparams.test_dir + "/*.wav")
            self.wav_pairs = list(itertools.combinations(test_wavs, 2))
            self.queue = queue
            start_new_thread(self.generate_data, ())

        else:
            raise ValueError("mode not supported")

    def vad_process(self, path):
        wav_id = os.path.splitext(os.path.basename(path))[0]
        audio, sample_rate = vad_ex.read_wave(path)
        vad = webrtcvad.Vad(1)
        frames = vad_ex.frame_generator(30, audio, sample_rate)
        frames = list(frames)
        segments = vad_ex.vad_collector(sample_rate, 30, 300, vad, frames)
        total_wav = b""
        for i, segment in enumerate(segments):
            total_wav += segment
            #print(wav_id + " : " + str(i) + "th segment appended")
        # Without writing, unpack total_wav into numpy [N,1] array
        # 16bit PCM 기준 dtype=np.int16
        wav_arr = np.frombuffer(total_wav, dtype=np.int16)
        #print("read audio data from byte string. np array of shape:"+str(wav_arr.shape))
        logmel_feats = logfbank(wav_arr, samplerate=sample_rate, nfilt=40)
        return logmel_feats

    def generate_spk(self, list, num_elements):
        batch = list[:num_elements]
        del list[:num_elements]
        list += batch
        return batch

    
    def is_invalid_spk(self, spk_id):
        # check if each speaker has more than at least self.hparams.num_utt_per_batch utterances
        spk_utt = [1 for pickle in self.pickles if re.search(spk_id, pickle)]
        num_utt = sum(spk_utt)
        if num_utt < self.hparams.num_utt_per_batch:
            return True
        else:
            return False

    def generate_data(self):
        while True:
            if self.mode == "train":
                if self.queue.qsize() > 10:
                    time.sleep(0.1)
                    continue;
                in_batch, target_batch = self.create_train_batch()
                self.queue.put([in_batch, target_batch])
            elif self.mode == "test":
                if self.queue.qsize() > 100:
                    time.sleep(0.1)
                    continue;
                wav1_data, wav2_data, match = self.create_test_batch()
                self.queue.put([wav1_data, wav2_data, match])

        self.queue.task_done()

    def extract_features(self, path):
        logmel_feats = self.vad_process(path)
        num_frames = self.hparams.segment_length * 100
        num_overlap_frames = num_frames * self.hparams.overlap_ratio
        total_len = logmel_feats.shape[0]
        num_dvectors = int((total_len - num_overlap_frames) // (num_frames - num_overlap_frames))
        print("num dvec:" + str(num_dvectors))
        dvectors = []
        for dvec_idx in range(num_dvectors):
            start_idx = int((num_frames - num_overlap_frames) * dvec_idx)
            end_idx = int(start_idx + num_frames)
            #print("first wav"+ " start_idx: " + str(start_idx))
            #print("second wav"+ " end_idx: " + str(end_idx))
            dvectors.append(logmel_feats[start_idx:end_idx, :])
        dvectors = np.asarray(dvectors)
        return dvectors

    
    def create_train_batch(self):
        num_frames = int(self.hparams.segment_length * 100)
        spk_batch = self.generate_spk(self.spk_names, self.hparams.num_spk_per_batch)
        target_batch = [spk for spk in range(self.hparams.num_spk_per_batch) for i in range(self.hparams.num_utt_per_batch)]
        #print("spk_batch: " + str(spk_batch))
        #print("target_batch: " + str(target_batch))
        in_batch = []

        for spk_id in spk_batch:
            if self.is_invalid_spk(spk_id):
                print("speaker id: " + spk_id + " has less than " + str(self.hparams.num_utt_per_batch) + " utt files")
                continue
            # speaker_pickle_files_list ['id10645_xG_tys7Wrxg_00003.pickle', 'id10645_xG_tys7Wrxg_00004.pickle'...]
            speaker_pickle_files_list = [file_name for file_name in os.listdir(self.hparams.in_dir + "/" + self.data_type) if re.search(spk_id, file_name) is not None]
            num_pickle_per_speaker = len(speaker_pickle_files_list)

            # list of indices in speaker_pickle_files_list
            utt_idx_list = random.choices(range(num_pickle_per_speaker), k=self.hparams.num_utt_per_batch)
            #print("utt_idx_list for " +str(spk_id)+" is " + str(utt_idx_list))
            for utt_idx in utt_idx_list:
                utt_pickle = speaker_pickle_files_list[utt_idx]
                utt_path = self.hparams.in_dir + "/" + self.data_type + "/" + utt_pickle
                with open(utt_path, "rb") as f:
                    load_dict = pickle.load(f)
                    total_logmel_feats = load_dict["LogMel_Features"]

                # random start point for every utterance
                start_idx = random.randrange(0, total_logmel_feats.shape[0] - num_frames)
                #print("start index:" + str(start_idx))

                # total logmel_feats is a numpy array of [num_frames, nfilt]
                # slice logmel_feats by 160 frames (apprx. 1.6s) results in [160, 40] np array
                logmel_feats = total_logmel_feats[start_idx:start_idx+num_frames, :]
                in_batch.append(logmel_feats)

        in_batch = np.asarray(in_batch)
        target_batch = np.asarray(target_batch)

        return in_batch, target_batch


    def create_infer_batch(self):
        # self.hparams.in_wav1, self.hparams.in_wav2 are full paths of the wav file
        # for ex) /home/hdd2tb/ninas96211/dev_wav_set/id10343_pCDWKHjQjso_00002.wav

        wav1_dvectors = self.extract_features(self.hparams.in_wav1)
        wav2_dvectors = self.extract_features(self.hparams.in_wav2)

        if os.path.basename(self.hparams.in_wav1).split("_")[0] == os.path.basename(self.hparams.in_wav2).split("_")[0]:
            match = True
        else:
            match = False

        print("wav1_dvectors.shape:" + str(wav1_dvectors.shape))
        print("wav2_dvectors.shape:" + str(wav2_dvectors.shape))
        print("match: " + str(match))
        return wav1_dvectors, wav2_dvectors, match

    def create_test_batch(self):
        wav_pair = self.wav_pairs.pop()

        wav1_dvectors = self.extract_features(wav_pair[0])
        wav2_dvectors = self.extract_features(wav_pair[1])

        if os.path.basename(wav_pair[0]).split("_")[0] == os.path.basename(wav_pair[1]).split("_")[0]:
            match = True
        else:
            match = False

        print("wav1_dvectors.shape:" + str(wav1_dvectors.shape))
        print("wav2_dvectors.shape:" + str(wav2_dvectors.shape))
        print("match: " + str(match))
        return wav1_dvectors, wav2_dvectors, match

        


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment_length", type=float, default=1.6, help="segment length in seconds")
    parser.add_argument("--in_dir", type=str, required=True, help="input data dir")
    args = parser.parse_args()
    feeder = Feeder(args)
    feeder.preprocess()
