import os
import shutil


def clear_tmp_folder():
    tmp_path = '/tmp/'
    for filename in os.listdir(tmp_path):
        file_path = os.path.join(tmp_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Failed to delete {file_path}. Reason: {e}')
