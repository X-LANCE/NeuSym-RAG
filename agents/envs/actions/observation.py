#coding=utf8

from typing import Dict, Any, Optional


class Observation():

    def __init__(self, obs_content: str, obs_type: str = 'text', mine_type: Optional[str] = None):
        """
        @param:
            obs_content: str, the text content or image base64 data of the observation.
            obs_type: str, the observation type should be one of ["text", "image"], default is "text".
        """
        self.obs_content = obs_content
        self.obs_type = obs_type
        assert self.obs_type in ['text', 'image'], f'Invalid observation type: {self.obs_type}'
        self.mine_type = mine_type


    def convert_to_message(self) -> Dict[str, Any]:
        if self.obs_type == 'text':
            msg_content = f'[Observation]:\n{self.obs_content}' if '\n' in str(self.obs_content) else f'[Observation]: {self.obs_content}'
        elif self.obs_type == 'image':
            self.mine_type = self.mine_type or 'image/jpeg'
            msg_content = [
                {
                    'type': 'text',
                    'text': '[Observation]: The extracted image is shown below.'
                },
                {
                    'type': 'image_url',
                    'image_url': {
                        'url': f'data:{self.mine_type};base64,' + self.obs_content
                    }
                }
            ]
        else:
            raise ValueError('Invalid observation type: ' + self.obs_type)
        return {'role': 'user', 'content': msg_content}
