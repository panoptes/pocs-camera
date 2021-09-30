# POCS Camera Service

A web based camera service for use with POCS.

The service is a thin-wrapper around the [gphoto2](http://www.gphoto.org/) command line utility that allows command arguments to be passed remotely via a valid JSON POST request.

## Arguments

Any valid argument to `gphoto2` can be given as the `arguments` key of a valid JSON post.

The service currently does not attempt to filter or parse the arguments, with the exception of the `--filename` argument.

### Filenames

If the `BASE_DIR` envvar is set when the service starts, any `--filename` argument will be saved in the given directory, even if an absolute path is specified. This is designed to allow for proper saving inside a docker container where the absolute path outside the container is mapped to a different path inside the container. See [Examples](#examples) for more details.

## Docker
<a name="docker"></a>

A Dockerfile is provided and a pre-made image is available at for `amd64` and `arm64` architectures:

```sh
docker pull gcr.io/panoptes-exp/pocs-camera
```

The container runs with the `panoptes` (`uid/gid` = `1000:1000`) and the images are saved into the `/images` directory, which should be mapped accordingly when the service is started. See also the information on the `BASE_DIR` env var in the [Examples](#examples) below.

The container also requires access to the USB bus of the host, which can mostly easily be accomplished with the `--privileged` option to `docker run`.

The service is available by default on port `6565`, which should also be provided at runtime.

```sh
docker run --privileged -p 6565:6565 -v "$PWD/images:/images"
```

## Examples
<a name="examples"></a>

### Running the service

The service can be run directly via the command line or as a Docker container (see [Docker](#docker) above for example running in a container). 

#### Dependencies

If running from the command line, the `uvicorn` web server should be installed (it is not included in the requirements file). This can be installed directly with python:

```py
pip install uvicorn[standard]
```

#### Starting the service

The service can be started from the command line with:

```sh
uvicorn main:app --port 6565
```

### Using the service

The service provides a single endpoint at the root of the server (i.e. `/`) and can be used with anything that can make valid JSON POST requests to the server. 

The endpoint expects the `arguments` field as single string containing the arguments that would normally be passed to `gphoto2`.

The below examples assume the service is running at the ip `192.168.1.100` on the default port `6565`.

> 💡 Note: The service blocks while using gphoto2, so if an exposure is 60 seconds long then there will be no response from the server during that time.
>
> An async service is planned for the future.

#### From python

```py
import requests

# Setup service info
host = '192.168.1.100'
port = 6565
endpoint = f'{host}:{port}'

# Build gphoto2 command
cmd_args = '--capture-image-and-download --filename test_image_01.cr2'

response = requests.post(endpoint, json=dict(arguments=cmd_args))
if response.ok:
    output = response.json()['output']
    print(f'Output from command: {output}')
```

See also the [POCS](#pocs) example below.

#### From the command line

You can use [HTTPie](https://httpie.io/) from the command line:

```sh
http 192.168.1.100:6565 arguments="--capture-image-and-download --filename test_image_01.cr2"
```

#### From POCS
<a name="pocs"></a>

If you are using [POCS](https://github.com/panoptes/POCS) to control your observatory you can instantiate a remote camera:

```py
from panoptes.pocs.camera.gphoto.remote import Camera

# Setup service info
host = '192.168.1.100'
port = 6565
endpoint = f'{host}:{port}'

# Create the camera
cam = Camera(endpoint=endpoint, name='Cam00')

# Take a picture
cam.take_exposure(seconds=2, filename='test_image_02.cr2')
```
